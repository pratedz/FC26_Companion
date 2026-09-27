cmake_minimum_required(VERSION 3.16)
project(LECompanionInject C)

add_library(LECompanionInject SHARED
  le_companion_inject.c
  protocol_core.c
)
target_compile_definitions(LECompanionInject PRIVATE LE_COMPANION_INJECT_EXPORTS)
target_include_directories(LECompanionInject PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
set_target_properties(LECompanionInject PROPERTIES
  OUTPUT_NAME "LECompanionInject"
  PREFIX ""
)
install(TARGETS LECompanionInject RUNTIME DESTINATION bin LIBRARY DESTINATION lib)
