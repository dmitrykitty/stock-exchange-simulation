// Contract K1: exchange interface (order books of all assets + matching).
// Semantics: docs/model/contracts.md, section K1. Tests: tests/contracts/test_market_contract.py.
#pragma once

#include <cstddef>
#include <memory>
#include <vector>

#include "lob_core/types.hpp"

namespace lob {

class Exchange {
public:
    explicit Exchange(std::size_t n_assets);
    ~Exchange();
    Exchange(const Exchange&) = delete;
    Exchange& operator=(const Exchange&) = delete;

    std::size_t n_assets() const noexcept;

    // Accepts one order, matches it with price-time priority, appends trades to fills_out.
    Report submit(const OrderRequest& request, std::vector<Fill>& fills_out);

    // The result MUST be identical to calling submit() for each request in order.
    std::vector<Report> submit_batch(const std::vector<OrderRequest>& requests, std::vector<Fill>& fills_out);

    // Returns the cancelled remaining quantity; 0 if the order is gone (filled, cancelled, unknown).
    Qty cancel(OrderId order_id);

    Level top(AssetId asset) const;
    std::vector<Level> depth(AssetId asset, std::size_t levels) const;  // exactly `levels` elements
    LastTrade last_trade(AssetId asset) const;

private:
    struct Impl;  // implementation details are up to the engine owner
    std::size_t n_assets_;
    std::unique_ptr<Impl> impl_;
};

}  // namespace lob
