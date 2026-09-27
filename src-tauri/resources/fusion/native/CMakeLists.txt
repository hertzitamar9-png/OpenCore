cmake_minimum_required(VERSION 3.20)
project(TwinCoreNative LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT WIN32 OR NOT DEFINED LLAMA_SOURCE OR NOT DEFINED LLAMA_BUILD)
  message(FATAL_ERROR "Requires Windows and the pinned LLAMA_SOURCE/LLAMA_BUILD")
endif()
add_library(twincore SHARED twincore.cpp)
target_compile_definitions(twincore PRIVATE LLAMA_SHARED GGML_SHARED)
target_include_directories(twincore PRIVATE "${LLAMA_SOURCE}/include" "${LLAMA_SOURCE}/src"
  "${LLAMA_SOURCE}/ggml/include" "${LLAMA_SOURCE}/ggml/src")
target_link_libraries(twincore PRIVATE "${LLAMA_BUILD}/src/llama.lib"
  "${LLAMA_BUILD}/ggml/src/ggml-base.lib" "${LLAMA_BUILD}/ggml/src/ggml-cpu.lib")
option(TWINCORE_BUILD_PROBE "Build CPU integration fixture; not a model qualification" OFF)
if(TWINCORE_BUILD_PROBE)
  add_library(twincore-probe SHARED "${CMAKE_CURRENT_SOURCE_DIR}/../../../../tests/fusion/native_projection_probe.cpp")
  target_compile_definitions(twincore-probe PRIVATE GGML_SHARED)
  target_include_directories(twincore-probe PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}"
    "${LLAMA_SOURCE}/ggml/include" "${LLAMA_SOURCE}/ggml/src")
  target_link_libraries(twincore-probe PRIVATE "${LLAMA_BUILD}/ggml/src/ggml-base.lib"
    "${LLAMA_BUILD}/ggml/src/ggml-cpu.lib" "${LLAMA_BUILD}/ggml/src/ggml.lib")
endif()
