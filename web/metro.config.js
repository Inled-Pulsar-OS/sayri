const { getDefaultConfig } = require("expo/metro-config");

const config = getDefaultConfig(__dirname);

// react-native-skia on web loads CanvasKit (.wasm) assets.
config.resolver.assetExts.push("wasm");

module.exports = config;
