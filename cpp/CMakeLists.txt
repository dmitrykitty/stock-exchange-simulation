cmake_minimum_required(VERSION 3.18...3.31)
project(lob_core LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Plain C++ library (no Python): used by the Python module and by the C++ tests.
add_library(lob_core_lib STATIC src/exchange.cpp)
target_include_directories(lob_core_lib PUBLIC include)
set_target_properties(lob_core_lib PROPERTIES POSITION_INDEPENDENT_CODE ON)

# Python module (built by scikit-build-core: `pip install -e cpp/`).
if(SKBUILD)
  find_package(Python 3.11 COMPONENTS Interpreter Development.Module REQUIRED)
  execute_process(
    COMMAND "${Python_EXECUTABLE}" -m nanobind --cmake_dir
    OUTPUT_STRIP_TRAILING_WHITESPACE OUTPUT_VARIABLE nanobind_ROOT)
  find_package(nanobind CONFIG REQUIRED)
  nanobind_add_module(lob_core bindings/module.cpp)
  target_link_libraries(lob_core PRIVATE lob_core_lib)
  install(TARGETS lob_core LIBRARY DESTINATION .)
endif()

# Optional C++ tests: cmake -S cpp -B cpp/build -DLOB_CORE_BUILD_TESTS=ON
option(LOB_CORE_BUILD_TESTS "Build C++ unit tests (Catch2)" OFF)
if(LOB_CORE_BUILD_TESTS)
  include(FetchContent)
  FetchContent_Declare(Catch2 GIT_REPOSITORY https://github.com/catchorg/Catch2.git GIT_TAG v3.7.1)
  FetchContent_MakeAvailable(Catch2)
  add_executable(lob_core_tests tests/test_exchange.cpp)
  target_link_libraries(lob_core_tests PRIVATE lob_core_lib Catch2::Catch2WithMain)
  include(CTest)
  add_test(NAME lob_core_tests COMMAND lob_core_tests)
endif()
