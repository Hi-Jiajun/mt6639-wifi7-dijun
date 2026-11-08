# Xposed API stubs — third-party attribution

This directory contains minimal compile-time stubs of the public Xposed API
(`de.robv.android.xposed.*`): class names, method signatures and constants only,
with no upstream implementation.

The Xposed API is part of the XposedBridge project by rovo89 and is distributed
under the Apache License 2.0:

- https://github.com/rovo89/XposedBridge (API package `de.robv.android.xposed`)

These stubs exist only so the LSPosed module can be compiled and tested offline.
They are not covered by this repository's GPL-3.0 license; the upstream Apache
License 2.0 terms apply. If the upstream API changes, replace the stubs with the
official `XposedBridgeApi` artifact.
