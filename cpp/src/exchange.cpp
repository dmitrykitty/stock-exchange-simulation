// Order books and matching for all assets. Semantics: docs/plans/phases/F3_exchange_engine.md, section 2.
//
// Layout (one book per asset):
//   side   = std::map<price, Queue>, ordered best-first (bids: descending, asks: ascending),
//   Queue  = total resting quantity + FIFO std::list of resting orders,
//   index  = order_id -> (asset, side, price, list iterator) for O(1) cancel after an O(log P) level lookup,
//   own    = owner -> {price -> resting orders count} per side, for the self-trade check.
// unordered_map is used only for lookups, never iterated, so results do not depend on hashing.
#include "lob_core/exchange.hpp"

#include <algorithm>
#include <functional>
#include <limits>
#include <list>
#include <map>
#include <stdexcept>
#include <string>
#include <unordered_map>

namespace lob {
namespace {

struct Resting {
    OrderId id;
    Owner owner;
    Qty remaining;
};

struct Queue {
    Qty total = 0;
    std::list<Resting> orders;
};

using OwnPrices = std::unordered_map<Owner, std::map<Price, std::uint32_t>>;

template <class BestFirst>
struct BookSide {
    std::map<Price, Queue, BestFirst> levels;
    OwnPrices own;

    std::list<Resting>::iterator add(Price price, const Resting& order) {
        Queue& q = levels[price];
        q.total += order.remaining;
        q.orders.push_back(order);
        ++own[order.owner][price];
        return std::prev(q.orders.end());
    }

    void forget_own(Owner owner, Price price) {
        auto by_owner = own.find(owner);
        auto by_price = by_owner->second.find(price);
        if (--by_price->second == 0) {
            by_owner->second.erase(by_price);
            if (by_owner->second.empty()) own.erase(by_owner);
        }
    }

    Qty remove(Price price, std::list<Resting>::iterator it) {
        auto level = levels.find(price);
        const Qty remaining = it->remaining;
        level->second.total -= remaining;
        forget_own(it->owner, price);
        level->second.orders.erase(it);
        if (level->second.orders.empty()) levels.erase(level);
        return remaining;
    }

    // Prices (ascending) at which `owner` rests on this side; nullptr if none.
    const std::map<Price, std::uint32_t>* own_prices(Owner owner) const {
        auto it = own.find(owner);
        return it == own.end() ? nullptr : &it->second;
    }
};

using Bids = BookSide<std::greater<Price>>;
using Asks = BookSide<std::less<Price>>;

struct Book {
    Bids bids;
    Asks asks;
    LastTrade last;
};

struct Locator {
    AssetId asset;
    std::int8_t side;
    Price price;
    std::list<Resting>::iterator it;
};

bool valid_tif(std::uint8_t tif) { return tif == GTC || tif == IOC || tif == GTD; }

template <class Side>
void fill_levels(const Side& side, std::vector<Level>& out, bool bid) {
    std::size_t k = 0;
    for (auto it = side.levels.begin(); it != side.levels.end() && k < out.size(); ++it, ++k) {
        if (bid) {
            out[k].bid = it->first;
            out[k].bid_qty = it->second.total;
        } else {
            out[k].ask = it->first;
            out[k].ask_qty = it->second.total;
        }
    }
}

constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();

}  // namespace

struct Exchange::Impl {
    std::vector<Book> books;
    std::unordered_map<OrderId, Locator> index;
    OrderId next_order_id = 1;
    Seq next_seq = 1;

    explicit Impl(std::size_t n_assets) : books(n_assets) {}

    const Book& book(AssetId asset) const {
        if (asset >= books.size()) throw std::out_of_range("asset " + std::to_string(asset) + " out of range");
        return books[asset];
    }

    template <class Opposite, class Same, class Crosses>
    Report match(Book& book, Opposite& opposite, Same& same, const OrderRequest& r, OrderId order_id,
                 std::vector<Fill>& fills, Crosses crosses) {
        Qty remaining = r.qty;
        Qty notional = 0;
        while (remaining > 0 && !opposite.levels.empty()) {
            auto level = opposite.levels.begin();
            const Price price = level->first;
            if (!crosses(price)) break;
            Queue& queue = level->second;
            while (remaining > 0 && !queue.orders.empty()) {
                Resting& maker = queue.orders.front();
                const Qty q = std::min(remaining, maker.remaining);
                fills.push_back(Fill{next_seq, r.asset, price, q, order_id, maker.id, r.owner, maker.owner, r.side});
                book.last = LastTrade{next_seq, price};
                ++next_seq;
                remaining -= q;
                maker.remaining -= q;
                queue.total -= q;
                notional += price * q;
                if (maker.remaining == 0) {
                    opposite.forget_own(maker.owner, price);
                    index.erase(maker.id);
                    queue.orders.pop_front();
                }
            }
            if (queue.orders.empty()) opposite.levels.erase(level);
        }

        const Qty filled = r.qty - remaining;
        Qty resting = 0;
        if (remaining > 0 && r.tif != IOC) {
            auto it = same.add(r.limit, Resting{order_id, r.owner, remaining});
            index.emplace(order_id, Locator{r.asset, r.side, r.limit, it});
            resting = remaining;
        }

        std::uint8_t status;
        if (filled == r.qty) {
            status = FILLED;
        } else if (filled == 0) {
            status = resting ? RESTING : CANCELLED_IOC;
        } else {
            status = resting ? PARTIAL_RESTING : PARTIAL_CANCELLED;
        }
        const double avg = filled > 0 ? static_cast<double>(notional) / static_cast<double>(filled) : kNaN;
        return Report{order_id, status, filled, resting, avg};
    }

    Report submit(const OrderRequest& r, std::vector<Fill>& fills) {
        const OrderId order_id = next_order_id++;
        if (r.asset >= books.size() || (r.side != BUY && r.side != SELL) || r.qty <= 0 || r.limit <= 0 ||
            !valid_tif(r.tif)) {
            return Report{order_id, REJECTED_INVALID, 0, 0, kNaN};
        }
        Book& b = books[r.asset];
        if (r.side == BUY) {
            // Self-trade: an own ask at or below the incoming limit would be matched.
            if (auto own = b.asks.own_prices(r.owner); own && own->begin()->first <= r.limit) {
                return Report{order_id, REJECTED_SELF_TRADE, 0, 0, kNaN};
            }
            return match(b, b.asks, b.bids, r, order_id, fills, [&](Price ask) { return ask <= r.limit; });
        }
        // Self-trade: an own bid at or above the incoming limit would be matched.
        if (auto own = b.bids.own_prices(r.owner); own && own->rbegin()->first >= r.limit) {
            return Report{order_id, REJECTED_SELF_TRADE, 0, 0, kNaN};
        }
        return match(b, b.bids, b.asks, r, order_id, fills, [&](Price bid) { return bid >= r.limit; });
    }

    Qty cancel(OrderId order_id) {
        auto found = index.find(order_id);
        if (found == index.end()) return 0;
        const Locator loc = found->second;
        index.erase(found);
        Book& b = books[loc.asset];
        return loc.side == BUY ? b.bids.remove(loc.price, loc.it) : b.asks.remove(loc.price, loc.it);
    }
};

Exchange::Exchange(std::size_t n_assets) : n_assets_(n_assets), impl_(std::make_unique<Impl>(n_assets)) {
    if (n_assets == 0) throw std::invalid_argument("n_assets must be positive");
}

Exchange::~Exchange() = default;

std::size_t Exchange::n_assets() const noexcept { return n_assets_; }

Report Exchange::submit(const OrderRequest& request, std::vector<Fill>& fills_out) {
    return impl_->submit(request, fills_out);
}

std::vector<Report> Exchange::submit_batch(const std::vector<OrderRequest>& requests, std::vector<Fill>& fills_out) {
    std::vector<Report> reports;
    reports.reserve(requests.size());
    for (const OrderRequest& r : requests) reports.push_back(impl_->submit(r, fills_out));
    return reports;
}

Qty Exchange::cancel(OrderId order_id) { return impl_->cancel(order_id); }

Level Exchange::top(AssetId asset) const { return depth(asset, 1).front(); }

std::vector<Level> Exchange::depth(AssetId asset, std::size_t levels) const {
    std::vector<Level> out(levels);
    const Book& b = impl_->book(asset);
    fill_levels(b.bids, out, true);
    fill_levels(b.asks, out, false);
    return out;
}

LastTrade Exchange::last_trade(AssetId asset) const { return impl_->book(asset).last; }

}  // namespace lob
