// Python module `lob_core`. A thin layer: conversion to numpy arrays happens in
// src/marketsim/market/cpp_exchange.py. When speed matters, return ready buffers
// (e.g. nb::bytes holding an array of Fill structs) instead of object lists; the Python contract stays the same.
#include <nanobind/nanobind.h>
#include <nanobind/stl/pair.h>
#include <nanobind/stl/tuple.h>
#include <nanobind/stl/vector.h>

#include "lob_core/exchange.hpp"

namespace nb = nanobind;
using namespace nb::literals;

NB_MODULE(lob_core, m) {
    m.doc() = "Order book and matching engine (contract K1).";
    m.attr("CONTRACT_VERSION") = lob::CONTRACT_VERSION;
    m.attr("NO_PRICE") = lob::NO_PRICE;

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
