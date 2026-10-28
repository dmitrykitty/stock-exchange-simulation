// C++ tests (Catch2). The main semantic tests live in Python (tests/contracts/test_market_contract.py)
// and run against both implementations: PyExchange and lob_core. This file holds fast core-only tests.
#include <catch2/catch_test_macros.hpp>

#include "lob_core/exchange.hpp"

TEST_CASE("exchange is constructible") {
    lob::Exchange ex(3);
    REQUIRE(ex.n_assets() == 3);
}
