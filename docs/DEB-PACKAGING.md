# Debian/Ubuntu packaging guide

This project ships native Debian packaging metadata in `debian/` and builds two packages:

- `oneconnect` (CLI)
- `oneconnect-gui` (tray/profile manager)

## Build prerequisites

On Debian/Ubuntu build hosts:

```bash
sudo apt update
sudo apt install -y \
  build-essential \
  debhelper \
  dh-python \
  pybuild-plugin-pyproject \
  python3-all \
  python3-setuptools \
  devscripts \
  dpkg-dev
```

## Build locally

From repo root:

```bash
dpkg-buildpackage -us -uc -b
```

Artifacts are written in the parent directory, for example:

- `../oneconnect_*_all.deb`
- `../oneconnect-gui_*_all.deb`
- `../oneconnect-linux_*_amd64.buildinfo` (or arm64 depending on host)

## Build for amd64 and arm64

Because this is architecture-independent Python code, package binaries are `Architecture: all`.
For reproducibility and policy checks, still build once on each target host type:

- build on `amd64` host/chroot
- build on `arm64` host/chroot

Recommended tools: `sbuild` or `pbuilder`.

## Install and smoke test

```bash
sudo apt install ../oneconnect_*_all.deb ../oneconnect-gui_*_all.deb
oneconnect --help
oneconnect-gui --manage-profiles
```

Important:

- Prefer `apt install ./package.deb` over `dpkg -i package.deb`. `apt` resolves
  dependencies automatically; `dpkg` does not.
- On modern Ubuntu, the old `policykit-1` package name is obsolete; `pkexec`
  / `polkitd` are used instead.

## Distribution options

### 1) Publish release artifacts

Upload built `.deb` files in GitHub Releases for direct download.

### 2) APT repository (recommended for end users)

Host an apt repository with `reprepro` or `aptly`, publish:

- signing key
- source entry
- install instructions:

```bash
sudo apt update
sudo apt install oneconnect oneconnect-gui
```
