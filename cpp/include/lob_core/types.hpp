// Contract K1: types shared by C++ and Python.
// Enum values MUST match src/marketsim/market/api.py (checked by tests/contracts/test_market_contract.py).
#pragma once

#include <cstdint>
#include <limits>
#include <stdexcept>

namespace lob {

inline constexpr int CONTRACT_VERSION = 1;

using Price = std::int64_t;    // price in ticks (1 tick = 0.01 USD)
using Qty = std::int64_t;      // number of shares
using OrderId = std::uint64_t; // assigned by the exchange: 1, 2, 3, ... (rejected orders included)
using Owner = std::uint32_t;   // agent id
using AssetId = std::uint16_t; // 0 .. n_assets-1
using Seq = std::uint64_t;     // trade number: 1, 2, 3, ... (shared by all assets)

// Empty side of the book / no trade yet.
inline constexpr Price NO_PRICE = std::numeric_limits<Price>::min();

enum Side : std::int8_t { BUY = 1, SELL = -1 };

// GTD behaves like GTC inside the book; expiry is done by the gateway (MarketGateway) via cancel().
enum TimeInForce : std::uint8_t { GTC = 0, IOC = 1, GTD = 2 };

enum Status : std::uint8_t {
    RESTING = 0,              // nothing filled, order rests in the book
    FILLED = 1,               // fully filled
    PARTIAL_RESTING = 2,      // partially filled, remainder rests in the book
    PARTIAL_CANCELLED = 3,    // IOC: partially filled, remainder cancelled
    CANCELLED_IOC = 4,        // IOC: nothing filled
    REJECTED_SELF_TRADE = 10, // own order on the opposite side would have been matched
    REJECTED_FUNDS = 11,      // gateway only: insufficient cash / shares
    REJECTED_INVALID = 12,    // invalid fields (qty <= 0, bad side, bad asset, limit <= 0)
};

struct OrderRequest {
    AssetId asset = 0;
    Owner owner = 0;
    std::int8_t side = BUY;
    Qty qty = 0;
    Price limit = 0;          // market order: protection price (IOC + limit)
    std::uint8_t tif = GTC;
};

struct Fill {
    Seq seq = 0;
    AssetId asset = 0;
    Price price = 0;          // always the price of the resting (maker) order
    Qty qty = 0;
    OrderId taker_order = 0;
    OrderId maker_order = 0;
    Owner taker_owner = 0;
    Owner maker_owner = 0;
    std::int8_t taker_side = BUY;
};

struct Report {
    OrderId order_id = 0;
    std::uint8_t status = RESTING;
    Qty filled_qty = 0;
    Qty resting_qty = 0;
    double avg_price = 0.0;   // average execution price in ticks; NaN if nothing filled
};

struct Level {                // one depth level; missing level: NO_PRICE and 0
    Price bid = NO_PRICE;
    Qty bid_qty = 0;
    Price ask = NO_PRICE;
    Qty ask_qty = 0;
};

struct LastTrade {
    Seq seq = 0;              // 0 if there was no trade
    Price price = NO_PRICE;
};

// Thrown by unimplemented methods; translated to NotImplementedError in Python.
struct NotImplemented : std::logic_error {
    using std::logic_error::logic_error;
};

}  // namespace lob
