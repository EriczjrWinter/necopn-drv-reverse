/* compiler.h -- minimal shim for compiling NP2's fmgen in a standalone DLL. */
#pragma once

#define SUPPORT_FMGEN 1

/* 预定义 fmgen_types.h 守卫 -> 它整段 #if 被跳过，不再重复 typedef / #define __stdcall */
#define win32_types_h

/* ---- 基础整数类型 ---- */
typedef unsigned char   uint8;
typedef unsigned short  uint16;
typedef unsigned int    uint32;
typedef signed char     int8;
typedef signed short    int16;
typedef signed int      int32;

typedef signed char     sint8;
typedef signed short    sint16;
typedef signed int      sint32;

typedef unsigned char   uchar;
typedef unsigned short  ushort;
typedef unsigned int    uint;
typedef unsigned long   ulong;

typedef signed int      SINT;
typedef signed int      SINT32;
typedef unsigned int    UINT32;
typedef signed short    SINT16;
typedef unsigned short  UINT16;
typedef signed char     SINT8;
typedef unsigned char   UINT8;

/* ---- Windows 兼容类型和常量（fmgen file.cpp / opna.cpp 用到）---- */
#define MAX_PATH 260
typedef unsigned long DWORD;
typedef void*         HANDLE;
typedef int           BOOL;

/* ---- 调用约定：MinGW x64 只有一种调用约定，全部置空 ---- */
#define SOUNDCALL
#define FASTCALL
#define STRCALL
#define INLINE    inline
#define BRESULT   uint8

/* ---- NP2 辅助宏 ---- */
#define _T(x)        x
#define OEMCHAR      char
#define OEMTEXT(s)   s
#define OEMSPRINTF   sprintf
#define OEMSTRLEN    strlen
#define __ASSERT(s)  ((void)0)
#define GETTICK()    0

/* MSVC 老版本 snprintf 别名（这里不会用到，保留以防） */
#if defined(_MSC_VER) && _MSC_VER < 1900
#define snprintf  _snprintf
#define vsnprintf _vsnprintf
#endif

#ifndef _WIN32
#   define __stdcall
#endif
