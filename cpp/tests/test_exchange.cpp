// C++ tests (Catch2). The main semantic tests live in Python (tests/contracts/test_market_contract.py)
// and run against both implementations: PyExchange and lob_core. This file holds fast core-only tests.
#include <catch2/catch_test_macros.hpp>

#include <cmath>
#include <stdexcept>

#include "lob_core/exchange.hpp"

namespace {

lob::OrderRequest order(lob::AssetId asset, lob::Owner owner, std::int8_t side, lob::Qty qty, lob::Price limit,
                        std::uint8_t tif = lob::GTC) {
    return lob::OrderRequest{asset, owner, side, qty, limit, tif};
}

}  // namespace

TEST_CASE("exchange is constructible") {
    lob::Exchange ex(3);
    REQUIRE(ex.n_assets() == 3);
    REQUIRE_THROWS_AS(lob::Exchange(0), std::invalid_argument);
}

TEST_CASE("price-time priority and maker price") {
    lob::Exchange ex(1);
    std::vector<lob::Fill> fills;
    ex.submit(order(0, 1, lob::SELL, 5, 102), fills);
    const auto first = ex.submit(order(0, 2, lob::SELL, 5, 101), fills);
    ex.submit(order(0, 3, lob::SELL, 5, 101), fills);
    const auto r = ex.submit(order(0, 4, lob::BUY, 7, 103), fills);
    REQUIRE(r.status == lob::FILLED);
    REQUIRE(fills.size() == 2);
    REQUIRE(fills[0].maker_order == first.order_id);
    REQUIRE(fills[0].price == 101);
    REQUIRE(fills[1].qty == 2);
    REQUIRE(ex.top(0).ask == 101);
    REQUIRE(ex.top(0).ask_qty == 3);
}

TEST_CASE("ioc remainder is cancelled and average price is reported") {
    lob::Exchange ex(1);
    std::vector<lob::Fill> fills;
    ex.submit(order(0, 1, lob::SELL, 5, 101), fills);
    ex.submit(order(0, 1, lob::SELL, 5, 102), fills);
    const auto r = ex.submit(order(0, 2, lob::BUY, 12, 102, lob::IOC), fills);
    REQUIRE(r.status == lob::PARTIAL_CANCELLED);
    REQUIRE(r.filled_qty == 10);
    REQUIRE(r.resting_qty == 0);
    REQUIRE(r.avg_price == 101.5);
    REQUIRE(ex.top(0).bid == lob::NO_PRICE);
}

TEST_CASE("self-trade is rejected before any state change") {
    lob::Exchange ex(1);
    std::vector<lob::Fill> fills;
    ex.submit(order(0, 1, lob::BUY, 5, 100), fills);
    const auto r = ex.submit(order(0, 1, lob::SELL, 5, 99), fills);
    REQUIRE(r.status == lob::REJECTED_SELF_TRADE);
    REQUIRE(std::isnan(r.avg_price));
    REQUIRE(fills.empty());
    REQUIRE(ex.top(0).bid_qty == 5);
}

TEST_CASE("cancel returns the remaining quantity once") {
    lob::Exchange ex(1);
    std::vector<lob::Fill> fills;
    const auto resting = ex.submit(order(0, 1, lob::SELL, 10, 101), fills);
    ex.submit(order(0, 2, lob::BUY, 4, 101), fills);
    REQUIRE(ex.cancel(resting.order_id) == 6);
    REQUIRE(ex.cancel(resting.order_id) == 0);
    REQUIRE(ex.depth(0, 2)[0].ask == lob::NO_PRICE);
}

TEST_CASE("queries reject unknown assets") {
    lob::Exchange ex(1);
    REQUIRE_THROWS_AS(ex.top(1), std::out_of_range);
    REQUIRE_THROWS_AS(ex.last_trade(5), std::out_of_range);
}
