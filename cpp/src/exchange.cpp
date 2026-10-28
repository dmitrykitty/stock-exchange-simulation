// Implementation skeleton. Every method except the constructor throws lob::NotImplemented,
// so contract tests are skipped (not failed) until the engine is implemented.
#include "lob_core/exchange.hpp"

namespace lob {

struct Exchange::Impl {
    // Suggested layout (docs/plans/phases/F3_exchange_engine.md): two sides per asset; price levels in std::map
    // (or an array indexed by tick), a FIFO queue per level; order_id -> order index for O(1) cancel().
};

Exchange::Exchange(std::size_t n_assets) : n_assets_(n_assets), impl_(std::make_unique<Impl>()) {}
Exchange::~Exchange() = default;

std::size_t Exchange::n_assets() const noexcept { return n_assets_; }

Report Exchange::submit(const OrderRequest&, std::vector<Fill>&) {
    throw NotImplemented("lob_core: Exchange::submit is not implemented yet");
}

std::vector<Report> Exchange::submit_batch(const std::vector<OrderRequest>&, std::vector<Fill>&) {
    throw NotImplemented("lob_core: Exchange::submit_batch is not implemented yet");
}

Qty Exchange::cancel(OrderId) { throw NotImplemented("lob_core: Exchange::cancel is not implemented yet"); }

Level Exchange::top(AssetId) const { throw NotImplemented("lob_core: Exchange::top is not implemented yet"); }

std::vector<Level> Exchange::depth(AssetId, std::size_t) const {
    throw NotImplemented("lob_core: Exchange::depth is not implemented yet");
}

LastTrade Exchange::last_trade(AssetId) const {
    throw NotImplemented("lob_core: Exchange::last_trade is not implemented yet");
}

}  // namespace lob
