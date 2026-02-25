cmake_minimum_required(VERSION 3.16)
project(ids_stall_test LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# ── IDS Peak SDK paths ──────────────────────────────────────────
# Auto-detect the IDS Peak installation
set(IDS_PEAK_DIR "C:/Program Files/IDS/ids_peak" CACHE PATH "IDS Peak SDK root")

# API (ids_peak)
set(IDS_PEAK_API_INCLUDE "${IDS_PEAK_DIR}/generic_sdk/api/include")
set(IDS_PEAK_API_LIB_DIR "${IDS_PEAK_DIR}/generic_sdk/api/lib/x86_64")

# Common
set(IDS_PEAK_COMMON_INCLUDE "${IDS_PEAK_DIR}/common/include")

# ── Verify paths exist ──────────────────────────────────────────
if(NOT EXISTS "${IDS_PEAK_API_INCLUDE}/peak/peak.hpp")
    message(FATAL_ERROR
        "Cannot find IDS Peak headers at ${IDS_PEAK_API_INCLUDE}/peak/peak.hpp\n"
        "Set -DIDS_PEAK_DIR=<path> to your IDS Peak installation.")
endif()

if(NOT EXISTS "${IDS_PEAK_API_LIB_DIR}/ids_peak.lib")
    message(FATAL_ERROR
        "Cannot find ids_peak.lib at ${IDS_PEAK_API_LIB_DIR}\n"
        "Set -DIDS_PEAK_DIR=<path> to your IDS Peak installation.")
endif()

# ── Executable ───────────────────────────────────────────────────
add_executable(ids_stall_test ids_stall_test.cpp)

target_include_directories(ids_stall_test PRIVATE
    "${IDS_PEAK_API_INCLUDE}"
    "${IDS_PEAK_COMMON_INCLUDE}"
)

target_link_libraries(ids_stall_test PRIVATE
    "${IDS_PEAK_API_LIB_DIR}/ids_peak.lib"
)

# ── Copy DLLs to output directory ────────────────────────────────
# The executable needs ids_peak.dll and its dependencies at runtime
file(GLOB IDS_PEAK_DLLS "${IDS_PEAK_API_LIB_DIR}/*.dll")
foreach(dll ${IDS_PEAK_DLLS})
    add_custom_command(TARGET ids_stall_test POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_if_different "${dll}" "$<TARGET_FILE_DIR:ids_stall_test>"
    )
endforeach()

# Set MSVC runtime to dynamic (/MD) to match IDS SDK
if(MSVC)
    set_property(TARGET ids_stall_test PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreadedDLL")
    # Disable some noisy warnings
    target_compile_options(ids_stall_test PRIVATE /W3 /wd4996)
endif()
