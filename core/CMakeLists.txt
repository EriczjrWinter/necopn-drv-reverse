cmake_minimum_required(VERSION 3.5...3.10)

project(NecoFMGen CXX)

string(COMPARE EQUAL "${NecoFMGen_SOURCE_DIR}" "${CMAKE_BINARY_DIR}" insource)
if(insource)
    file(REMOVE ${NecoFMGen_SOURCE_DIR}/CMakeCache.txt)
    file(REMOVE_RECURSE ${NecoFMGen_SOURCE_DIR}/CMakeFiles)
    message(FATAL_ERROR "
==== Build in source directory is not allowed! ====
Please run cmake configure from another directory, for example,
create a 'build' sub-directory, and run 'cmake ..' from it."
)
endif()

add_library(necopna_fmgen SHARED
    necopna_fmgen.cpp
    fmgen/fmgen_file.cpp
    fmgen/fmgen_fmgen.cpp
    fmgen/fmgen_fmgwrap.cpp
    fmgen/fmgen_fmtimer.cpp
    fmgen/fmgen_opm.cpp
    fmgen/fmgen_opna.cpp
    fmgen/fmgen_psg.cpp
)

set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${NecoFMGen_SOURCE_DIR})

target_include_directories(necopna_fmgen PRIVATE ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_CURRENT_SOURCE_DIR}/fmgen)
target_compile_definitions(necopna_fmgen PRIVATE -DSUPPORT_FMGEN)
