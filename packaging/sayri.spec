# ==============================================================================
# Sayri - RPM spec for Fedora
# Builds a noarch RPM from the Sayri package tree (usr/ + etc/).
#   rpmbuild -bb packaging/sayri.spec
# ==============================================================================

# The version the build script passes with --define wins over this default; a
# plain `rpmbuild -bb packaging/sayri.spec` still works and gets the default.
# It used to be a bare `%global sayri_version`, which rpm resolves *after* any
# --define from the command line, so the spec's own value silently won: the
# build asked for sayri-0.1.3.tar.gz while the script had written
# sayri-0.1.36.tar.gz, and the rpm failed in %prep on every version above
# 0.1.3. There is no .rpm in dist/ to show for it.
%global sayri_fallback_version 0.1.36
%global sayri_version %{?sayri_version:%{sayri_version}}
%global sayri_version %{!?sayri_version:%{sayri_fallback_version}}

Name:           sayri
Version:        %{sayri_version}
Release:        1%{?dist}
Summary:        Siri-like voice assistant with a reactive orb (GTK4)

License:        MIT
URL:            https://github.com/Inled-Pulsar-OS/sayri
Source0:        sayri-%{version}.tar.gz

BuildArch:      noarch

Requires:       python3
Requires:       python3-gobject
Requires:       python3-httpx
Requires:       gtk4
Requires:       gtk3
Requires:       webkitgtk6.0
Requires:       gtk4-layer-shell
Requires:       pipewire
Recommends:     whisper.cpp
Recommends:     grim
Recommends:     ydotool
Recommends:     bubblewrap
Recommends:     xdg-utils
Recommends:     libayatana-appindicator-gtk3

%description
Sayri is an always-available Siri-style AI voice assistant for Pulsar OS. A
reactive orb pinned to the corner of the screen reacts to your voice:
whisper.cpp transcribes what you say, text appears in an Apple-intelligence
style cajita next to the orb, and the query is sent to any OpenAI-compatible
API (OpenAI, Ollama, LM Studio, OpenClaw, ...). The answer is spoken back with
Piper TTS while the orb animates.

It ships with 5 levels of sandboxing, skills/plugins/gateways, a wake word and
a settings window. Speech and transcription models run 100% locally.

%prep
# %setup -c creates + cd's into sayri-<version>; the tarball is just the
# package tree (usr/ etc/ packaging/), extracted inside that dir.
%setup -c -q -n sayri-%{version}

%install
rm -rf %{buildroot}
install -d %{buildroot}
cp -a usr %{buildroot}/
if [ -d etc ]; then
    cp -a etc %{buildroot}/
fi

# Ensure binaries are executable.
chmod 0755 %{buildroot}/usr/bin/sayri
chmod 0755 %{buildroot}/usr/bin/sayri-indicator
chmod 0755 %{buildroot}/usr/bin/sayri-settings
chmod 0755 %{buildroot}/usr/bin/sayri-skills
chmod 0755 %{buildroot}/usr/bin/sayri-plugins
find %{buildroot}%{_datadir}/sayri/plugins -type f \( -name "*.py" -o -name "*.sh" \) -exec chmod 0755 {} + 2>/dev/null || :

%files
%doc README.md
%{_datadir}/sayri
%{_datadir}/applications/sayri.desktop
%{_datadir}/icons/hicolor/*
%{_datadir}/pixmaps/sayri.png
%{_datadir}/pixmaps/sayri.svg
%{_bindir}/sayri
%{_bindir}/sayri-indicator
%{_bindir}/sayri-settings
%{_bindir}/sayri-skills
%{_bindir}/sayri-plugins

%post
# Refresh the hicolor icon theme cache so the Sayri icon shows up in menus.
if [ -x /usr/bin/gtk-update-icon-cache ] && [ -d %{_datadir}/icons/hicolor ]; then
    /usr/bin/gtk-update-icon-cache -f -t %{_datadir}/icons/hicolor >/dev/null 2>&1 || :
fi
update-desktop-database %{_datadir}/applications >/dev/null 2>&1 || :
exit 0

%postun
if [ -x /usr/bin/gtk-update-icon-cache ] && [ -d %{_datadir}/icons/hicolor ]; then
    /usr/bin/gtk-update-icon-cache -f -t %{_datadir}/icons/hicolor >/dev/null 2>&1 || :
fi
exit 0

%changelog
* Sun Sep 27 2026 Jaime <info@inled.es> - 0.1.36-1
- Let the build script's version reach the spec, so the rpm stops asking for a
  tarball that was never written
* Thu Sep 03 2026 Jaice <info@inled.es> - 0.1.3-1
- Initial RPM packaging of Sayri 0.1.3
