# 随 Studio 一起发的动作包

升级后第一次打开「动作」页时，Studio 把这里的包放进「我的动作」（只进库，要点「装上」才登记到主程序）。放过的记在鸭子上 `/var/lib/microduck-studio/models/actions/.bundled.json`，只放一次，用户删了不会再放。

| 文件 | 动作 | 来源 | 许可 |
|---|---|---|---|
| `crouch.zip` | 深蹲（蹲下 3 秒，1.5 秒站回） | [Ley-WL/microduck-rl](https://github.com/Ley-WL/microduck-rl) `models/crouch_hd1910_823g_3000`，提交 4a4a34a5 | Apache-2.0（包里有 LICENSE） |
| `idle_chest.zip` | 抖一抖（3.1 秒，带记忆的模型） | 群友用本仓库训练台训的，作者同意随 Studio 发布 | 随 Studio 发布 |

两个都要幅度 1.0（包里的 `skill.toml` 已写好）。加新包：把 zip 放进来，在 `studio/models.py` 的 `BUNDLED_ACTIONS` 加一行 `(文件名, 中文名)`。这个目录必须在 `software/studio/studio/` 里面：升级包只收 Studio 的 studio/、web/、deps/、deploy/，放外面会被丢掉。
