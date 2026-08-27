"""Auth subsystem of mhc-desktop-backend.

Exposes the :class:`AuthProviderProtocol` and :class:`AuthUser` types
(also re-exported from :mod:`mhc_desktop_backend.protocols`) plus the
HTTP middleware + routes an :class:`AuthProviderProtocol` needs to
gate every request.

The default reference impl (``MockAuthProvider``) lives in
:mod:`mhc_desktop_deploy.impls.auth.mock` — the kernel never depends
on it. ``create_app(auth=YourProvider())`` activates enforcement;
without it every endpoint is public.
"""

from __future__ import annotations
