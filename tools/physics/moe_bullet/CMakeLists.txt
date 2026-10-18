# 原生 Bullet 物理烘焙器（照 saba MMDPhysics 一比一）。
# 构建：tools/moe_bullet/build.ps1。产物 work/moe_bullet_build/moe_bullet_bake.exe。
cmake_minimum_required(VERSION 3.20)
project(moe_bullet_bake CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()
# Bullet 作为子目录进来，对象路径才是相对的（理由见 third_party/bullet3/CMakeLists.txt）。
add_subdirectory(${CMAKE_CURRENT_SOURCE_DIR}/../../../third_party/bullet3 ${CMAKE_BINARY_DIR}/bullet3)
add_executable(moe_bullet_bake main.cpp)
target_link_libraries(moe_bullet_bake PRIVATE bullet_core)
if(MSVC)
  # /utf-8：源码含中文注释，cl 默认按 cp936 读会把部分换行吃掉，报一串莫名的语法错误（C4819）。
  target_compile_options(moe_bullet_bake PRIVATE /W3 /fp:precise /utf-8)
endif()
