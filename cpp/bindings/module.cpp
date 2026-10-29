// Python module `lob_core`.
//
// Two kinds of methods:
// - "*_packed": the fast path used by src/marketsim/market/cpp_exchange.py. Results are written into one
//   contiguous buffer of packed records whose byte layout equals the numpy dtypes of contract K1
//   (ORDER_DTYPE, FILL_DTYPE, REPORT_DTYPE, DEPTH_DTYPE). The buffer is handed to numpy without a copy
//   as a uint8 array; Python reinterprets it with `.view(dtype)`.
// - plain methods returning Python objects: convenient for debugging and benchmarks.
//
// WIRE_LAYOUT exports record sizes and field offsets so that the Python adapter can verify at import time
// that both sides agree on the layout.
#include <nanobind/nanobind.h>
#include <nanobind/ndarray.h>
#include <nanobind/stl/pair.h>
#include <nanobind/stl/tuple.h>
#include <nanobind/stl/vector.h>

#include <cstddef>
#include <cstring>
#include <stdexcept>
#include <vector>

#include "lob_core/exchange.hpp"

namespace nb = nanobind;
using namespace nb::literals;

namespace {

#pragma pack(push, 1)
struct WireOrder {
    std::uint16_t asset;
    std::uint32_t owner;
    std::int8_t side;
    std::int64_t qty;
    std::int64_t limit;
    std::uint8_t tif;
};

struct WireFill {
    std::uint64_t seq;
    std::uint16_t asset;
    std::int64_t price;
    std::int64_t qty;
    std::uint64_t taker_order;
    std::uint64_t maker_order;
    std::uint32_t taker_owner;
    std::uint32_t maker_owner;
    std::int8_t taker_side;
};

struct WireReport {
    std::uint64_t order_id;
    std::uint8_t status;
    std::int64_t filled_qty;
    std::int64_t resting_qty;
    double avg_price;
};

struct WireLevel {
    std::int64_t bid;
    std::int64_t bid_qty;
    std::int64_t ask;
    std::int64_t ask_qty;
};
#pragma pack(pop)

static_assert(sizeof(WireOrder) == 24);
static_assert(sizeof(WireFill) == 51);
static_assert(sizeof(WireReport) == 33);
static_assert(sizeof(WireLevel) == 32);

WireFill to_wire(const lob::Fill& f) {
    return WireFill{f.seq, f.asset, f.price, f.qty, f.taker_order, f.maker_order, f.taker_owner, f.maker_owner,
                    f.taker_side};
}

WireReport to_wire(const lob::Report& r) {
    return WireReport{r.order_id, r.status, r.filled_qty, r.resting_qty, r.avg_price};
}

WireLevel to_wire(const lob::Level& l) { return WireLevel{l.bid, l.bid_qty, l.ask, l.ask_qty}; }

using ByteArray = nb::ndarray<nb::numpy, std::uint8_t, nb::ndim<1>>;
using ByteInput = nb::ndarray<const std::uint8_t, nb::ndim<1>, nb::c_contig, nb::device::cpu>;

// Packs records into a heap buffer owned by a capsule; numpy keeps the capsule alive. None if empty.
template <class Wire, class Item>
nb::object pack(const std::vector<Item>& items) {
    if (items.empty()) return nb::none();
    auto* buffer = new std::vector<Wire>();
    buffer->reserve(items.size());
    for (const Item& item : items) buffer->push_back(to_wire(item));
    nb::capsule owner(buffer, [](void* p) noexcept { delete static_cast<std::vector<Wire>*>(p); });
    ByteArray array(reinterpret_cast<std::uint8_t*>(buffer->data()), {buffer->size() * sizeof(Wire)}, owner);
    return nb::cast(array);
}

std::vector<lob::OrderRequest> unpack_orders(const ByteInput& bytes) {
    const std::size_t n_bytes = bytes.shape(0);
    if (n_bytes % sizeof(WireOrder) != 0) {
        throw std::invalid_argument("order buffer size is not a multiple of the ORDER_DTYPE record size");
    }
    std::vector<lob::OrderRequest> out(n_bytes / sizeof(WireOrder));
    const std::uint8_t* data = bytes.data();
    for (std::size_t i = 0; i < out.size(); ++i) {
        WireOrder w;
        std::memcpy(&w, data + i * sizeof(WireOrder), sizeof(WireOrder));
        out[i] = lob::OrderRequest{w.asset, w.owner, w.side, w.qty, w.limit, w.tif};
    }
    return out;
}

#define LOB_FIELD(T, f) nb::make_tuple(#f, offsetof(T, f))

nb::dict record(std::size_t size, nb::tuple fields) {
    nb::dict d;
    d["size"] = size;
    d["fields"] = fields;
    return d;
}

nb::dict wire_layout() {
    nb::dict layout;
    layout["order"] = record(sizeof(WireOrder),
                             nb::make_tuple(LOB_FIELD(WireOrder, asset), LOB_FIELD(WireOrder, owner),
                                            LOB_FIELD(WireOrder, side), LOB_FIELD(WireOrder, qty),
                                            LOB_FIELD(WireOrder, limit), LOB_FIELD(WireOrder, tif)));
    layout["fill"] = record(sizeof(WireFill),
                            nb::make_tuple(LOB_FIELD(WireFill, seq), LOB_FIELD(WireFill, asset),
                                           LOB_FIELD(WireFill, price), LOB_FIELD(WireFill, qty),
                                           LOB_FIELD(WireFill, taker_order), LOB_FIELD(WireFill, maker_order),
                                           LOB_FIELD(WireFill, taker_owner), LOB_FIELD(WireFill, maker_owner),
                                           LOB_FIELD(WireFill, taker_side)));
    layout["report"] = record(sizeof(WireReport),
                              nb::make_tuple(LOB_FIELD(WireReport, order_id), LOB_FIELD(WireReport, status),
                                             LOB_FIELD(WireReport, filled_qty), LOB_FIELD(WireReport, resting_qty),
                                             LOB_FIELD(WireReport, avg_price)));
    layout["level"] = record(sizeof(WireLevel),
                             nb::make_tuple(LOB_FIELD(WireLevel, bid), LOB_FIELD(WireLevel, bid_qty),
                                            LOB_FIELD(WireLevel, ask), LOB_FIELD(WireLevel, ask_qty)));
    return layout;
}

#undef LOB_FIELD

}  // namespace

NB_MODULE(lob_core, m) {
    m.doc() = "Order book and matching engine (contract K1).";
    m.attr("CONTRACT_VERSION") = lob::CONTRACT_VERSION;
    m.attr("NO_PRICE") = lob::NO_PRICE;
    m.attr("WIRE_LAYOUT") = wire_layout();

    nb::register_exception_translator([](const std::exception_ptr& p, void*) {
        try {
            std::rethrow_exception(p);
        } catch (const lob::NotImplemented& e) {
            PyErr_SetString(PyExc_NotImplementedError, e.what());
        }
    });

    nb::class_<lob::OrderRequest>(m, "OrderRequest")
        .def(nb::init<>())
        .def_rw("asset", &lob::OrderRequest::asset)
        .def_rw("owner", &lob::OrderRequest::owner)
        .def_rw("side", &lob::OrderRequest::side)
        .def_rw("qty", &lob::OrderRequest::qty)
        .def_rw("limit", &lob::OrderRequest::limit)
        .def_rw("tif", &lob::OrderRequest::tif);

    nb::class_<lob::Fill>(m, "Fill")
        .def_ro("seq", &lob::Fill::seq)
        .def_ro("asset", &lob::Fill::asset)
        .def_ro("price", &lob::Fill::price)
        .def_ro("qty", &lob::Fill::qty)
        .def_ro("taker_order", &lob::Fill::taker_order)
        .def_ro("maker_order", &lob::Fill::maker_order)
        .def_ro("taker_owner", &lob::Fill::taker_owner)
        .def_ro("maker_owner", &lob::Fill::maker_owner)
        .def_ro("taker_side", &lob::Fill::taker_side);

    nb::class_<lob::Report>(m, "Report")
        .def_ro("order_id", &lob::Report::order_id)
        .def_ro("status", &lob::Report::status)
        .def_ro("filled_qty", &lob::Report::filled_qty)
        .def_ro("resting_qty", &lob::Report::resting_qty)
        .def_ro("avg_price", &lob::Report::avg_price);

    nb::class_<lob::Exchange>(m, "Exchange")
        .def(nb::init<std::size_t>(), "n_assets"_a)
        .def_prop_ro("n_assets", &lob::Exchange::n_assets)
        // Fast path: (order_id, status, filled_qty, resting_qty, avg_price, fills bytes or None).
        .def(
            "submit_packed",
            [](lob::Exchange& ex, lob::AssetId asset, lob::Owner owner, std::int8_t side, lob::Qty qty,
               lob::Price limit, std::uint8_t tif) {
                std::vector<lob::Fill> fills;
                const lob::Report r = ex.submit(lob::OrderRequest{asset, owner, side, qty, limit, tif}, fills);
                return nb::make_tuple(r.order_id, r.status, r.filled_qty, r.resting_qty, r.avg_price,
                                      pack<WireFill>(fills));
            },
            "asset"_a, "owner"_a, "side"_a, "qty"_a, "limit"_a, "tif"_a)
        // Fast path: ORDER_DTYPE records as bytes in, (REPORT_DTYPE bytes or None, FILL_DTYPE bytes or None) out.
        .def(
            "submit_batch_packed",
            [](lob::Exchange& ex, const ByteInput& orders) {
                const std::vector<lob::OrderRequest> requests = unpack_orders(orders);
                std::vector<lob::Fill> fills;
                const std::vector<lob::Report> reports = ex.submit_batch(requests, fills);
                return nb::make_tuple(pack<WireReport>(reports), pack<WireFill>(fills));
            },
            "orders"_a)
        // Fast path: DEPTH_DTYPE records as bytes, or None for zero levels.
        .def(
            "depth_packed",
            [](const lob::Exchange& ex, lob::AssetId asset, std::size_t levels) {
                return pack<WireLevel>(ex.depth(asset, levels));
            },
            "asset"_a, "levels"_a)
        .def(
            "submit",
            [](lob::Exchange& ex, lob::AssetId asset, lob::Owner owner, std::int8_t side, lob::Qty qty,
               lob::Price limit, std::uint8_t tif) {
                std::vector<lob::Fill> fills;
                lob::Report report = ex.submit(lob::OrderRequest{asset, owner, side, qty, limit, tif}, fills);
                return std::make_pair(report, std::move(fills));
            },
            "asset"_a, "owner"_a, "side"_a, "qty"_a, "limit"_a, "tif"_a)
        .def(
            "submit_batch",
            [](lob::Exchange& ex, const std::vector<lob::OrderRequest>& requests) {
                std::vector<lob::Fill> fills;
                std::vector<lob::Report> reports = ex.submit_batch(requests, fills);
                return std::make_pair(std::move(reports), std::move(fills));
            },
            "requests"_a)
        .def("cancel", &lob::Exchange::cancel, "order_id"_a)
        .def(
            "top",
            [](const lob::Exchange& ex, lob::AssetId asset) {
                lob::Level t = ex.top(asset);
                return std::make_tuple(t.bid, t.bid_qty, t.ask, t.ask_qty);
            },
            "asset"_a)
        .def(
            "depth",
            [](const lob::Exchange& ex, lob::AssetId asset, std::size_t levels) {
                std::vector<std::tuple<lob::Price, lob::Qty, lob::Price, lob::Qty>> out;
                for (const lob::Level& l : ex.depth(asset, levels)) out.emplace_back(l.bid, l.bid_qty, l.ask, l.ask_qty);
                return out;
            },
            "asset"_a, "levels"_a)
        .def(
            "last_trade",
            [](const lob::Exchange& ex, lob::AssetId asset) {
                lob::LastTrade t = ex.last_trade(asset);
                return std::make_tuple(t.seq, t.price);
            },
            "asset"_a);
}
