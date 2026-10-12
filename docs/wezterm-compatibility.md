# WezTerm compatibility

The shared WezTerm configuration omits `show_close_tab_button_in_tabs`.
[Upstream documents that option as nightly-only](https://wezterm.org/config/lua/config/show_close_tab_button_in_tabs.html),
and the [20240203-110809-5046fc22 stable configuration schema](https://github.com/wezterm/wezterm/blob/20240203-110809-5046fc22/config/src/config.rs)
does not recognize it. Setting it can produce `error converting lua table to Config`.
Omitting it leaves close-tab button visibility at WezTerm's default.

The Brewfile still selects `wezterm@nightly`; this compatibility fix does not
change the installation channel or any keybindings. In particular:

- Cmd+Shift+Left / Right still selects the previous / next WezTerm tab.
- Cmd+Option+arrow still moves between panes.
- Shift+Enter still sends a newline.

This is not a promise of compatibility with every historical release.
For example, [InputSelector requires 20230408-112425-69ae8472 or newer](https://wezterm.org/config/lua/keyassignment/InputSelector.html).

## Verification

Run the repository's source-level regression checks with:

```sh
python3 -m unittest discover -s tests -v
```

These checks guard the omitted nightly-only option and selected existing
bindings. They do not launch WezTerm or verify macOS keyboard delivery.
For local diagnosis, record `wezterm --version`, then load the actual config
with `wezterm --config-file "$HOME/.config/wezterm/wezterm.lua" show-keys`.
Confirm that the GUI uses the same app version, reload its configuration,
and test tab navigation with at least two tabs open.
