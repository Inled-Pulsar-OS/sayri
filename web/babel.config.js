module.exports = function (api) {
  api.cache(true);
  return {
    // babel-preset-expo auto-configures the reanimated/worklets plugin
    // according to the installed SDK version.
    presets: ["babel-preset-expo"],
  };
};
