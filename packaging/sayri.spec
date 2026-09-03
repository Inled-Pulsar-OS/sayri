# ==============================================================================
# Sayri - RPM spec for Fedora
# Builds a noarch RPM from the Sayri package tree (usr/ + etc/).
#   rpmbuild -bb packaging/sayri.spec
# ==============================================================================

%global sayri_version 0.1.3

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
cp -a etc %{buildroot}/

# Ensure binaries are executable and paths resolve.
chmod 0755 %{buildroot}/usr/bin/sayri
chmod 0755 %{buildroot}/usr/bin/sayri-indicator
chmod 0755 %{buildroot}/usr/bin/sayri-settings
chmod 0755 %{buildroot}/usr/bin/sayri-skills
chmod 0755 %{buildroot}/usr/bin/sayri-plugins
chmod 0755 %{buildroot}/usr/share/sayri/lib/sayri/domain/*.py
chmod 0644 %{buildroot}/usr/share/applications/sayri.desktop

%files
%doc README.md
%dir %{_datadir}/sayri
%{_datadir}/sayri/*
%{_datadir}/applications/sayri.desktop
%{_datadir}/icons/hicolor/*
%{_bindir}/sayri
%{_bindir}/sayri-indicator
%{_bindir}/sayri-settings
%{_bindir}/sayri-skills
%{_bindir}/sayri-plugins
%dir %{_sysconfdir}/xdg/autostart
%config(noreplace) %{_sysconfdir}/xdg/autostart/sayri.desktop

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
* Wed Sep 03 2026 Jaice <info@inled.es> - 0.1.3-1
- Initial RPM packaging of Sayri 0.1.3
