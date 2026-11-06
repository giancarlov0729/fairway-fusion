cmake_minimum_required(VERSION 3.13)

set(PICO_BOARD pico2_w CACHE STRING "Target board")

if(NOT DEFINED PICO_SDK_PATH)
    set(PICO_SDK_PATH "$ENV{PICO_SDK_PATH}")
endif()

if(NOT EXISTS "${PICO_SDK_PATH}/external/pico_sdk_import.cmake")
    message(FATAL_ERROR "Set PICO_SDK_PATH to a Raspberry Pi Pico SDK checkout.")
endif()

include("${PICO_SDK_PATH}/external/pico_sdk_import.cmake")

project(fairway_pico_usb C CXX ASM)

set(CMAKE_C_STANDARD 11)

pico_sdk_init()

add_executable(fairway_pico_usb main.c sequence.c)

pico_generate_pio_header(fairway_pico_usb ${CMAKE_CURRENT_LIST_DIR}/capture.pio)
target_link_libraries(fairway_pico_usb pico_stdlib hardware_pio hardware_watchdog pico_rand)

pico_enable_stdio_usb(fairway_pico_usb 1)

pico_enable_stdio_uart(fairway_pico_usb 0)

target_compile_definitions(
    fairway_pico_usb
    PRIVATE
    PICO_STDIO_USB_ENABLE_RESET_VIA_BAUD_RATE=0
    PICO_STDIO_USB_ENABLE_RESET_VIA_VENDOR_INTERFACE=0
)

pico_add_extra_outputs(fairway_pico_usb)