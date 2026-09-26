cmake_minimum_required(VERSION 3.20)
project(FusionCoreNative LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
if(NOT DEFINED LLAMA_SOURCE OR NOT DEFINED LLAMA_BUILD)
  message(FATAL_ERROR "LLAMA_SOURCE and LLAMA_BUILD must point to the pinned DuoCore llama.cpp tree")
endif()
add_library(fusioncore SHARED fusioncore.cpp)
target_compile_definitions(fusioncore PRIVATE LLAMA_SHARED GGML_SHARED)
target_include_directories(fusioncore PRIVATE
  "${LLAMA_SOURCE}/include" "${LLAMA_SOURCE}/src"
  "${LLAMA_SOURCE}/ggml/include" "${LLAMA_SOURCE}/ggml/src")
target_link_libraries(fusioncore PRIVATE
  "${LLAMA_BUILD}/src/llama.lib" "${LLAMA_BUILD}/ggml/src/ggml-base.lib")
