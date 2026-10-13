[app]
title = Twitch Clipper
package.name = twitchclipper
package.domain = com.errorpromts
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ttf
version = 1.0

requirements = python3,kivy,numpy,requests,ffmpeg

orientation = portrait
fullscreen = 1
android.permissions = WRITE_EXTERNAL_STORAGE,READ_EXTERNAL_STORAGE,INTERNET

android.api = 33
android.minapi = 24
android.ndk = 25b

[buildozer]
log_level = 2
warn_on_root = 1
