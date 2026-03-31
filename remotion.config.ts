import { Config } from "@remotion/cli/config";

Config.setVideoImageFormat("jpeg");
Config.setOverwriteOutput(true);
Config.setPixelFormat("yuv420p");
Config.setCrf(18);
Config.setCodec("h264");
Config.setScale(1);
Config.setConcurrency(4);
