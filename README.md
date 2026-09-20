<div align="center">

# PostgreSQL 技术内幕

**《The Internals of PostgreSQL》中文版**

深入理解 PostgreSQL 的进程、查询、并发控制、存储、WAL、备份与复制机制

[![在线阅读](https://img.shields.io/badge/在线阅读-pgint.vonng.com-336791?style=flat&logo=postgresql&logoColor=white)](https://pgint.vonng.com)
[![英文原著](https://img.shields.io/badge/英文原著-interdb.jp%2Fpg-4a5568?style=flat&logo=gitbook&logoColor=white)](https://www.interdb.jp/pg/)
[![整书打印](https://img.shields.io/badge/整书打印-PDF-8b5e3c?style=flat&logo=adobeacrobatreader&logoColor=white)](https://pgint.vonng.com/_print/)

<a href="https://pgint.vonng.com/toc/"><img src="static/cover.svg" alt="《PostgreSQL 技术内幕》封面" width="320"></a>

</div>

## 关于本书

PostgreSQL 是一个开源的关系型数据库，在世界各地被广泛用于各种目的。它是一个由多个子系统集成而来的巨大系统，每个子系统都包含着特殊的复杂功能，并与其它子系统相互协调工作。理解其内部原理对于管理和集成 PostgreSQL 而言至关重要，但其巨大性与复杂性让这一点变得相当困难。本书的目的正是解释这些子系统是如何工作的，并提供一幅关于 PostgreSQL 的全景图像。

本书由 [Hironobu Suzuki（鈴木 啓修）](https://www.interdb.jp/)原著，中文版经正规出版流程引进，作者专门为中文版撰写了序言。本仓库以冯若航、刘阳明、张文升于 2018 年完成的中文译稿为主体，并持续补译英文原著新增内容；全书从数据库集簇、进程和查询处理出发，依次进入并发控制、VACUUM、缓冲区、WAL、备份、物理流复制与逻辑复制。

## 目录

| # | 章节 | 源文件 |
|:---:|------|:---:|
| | [作者序](https://pgint.vonng.com/preface/) | [preface.md](content/preface.md) |
| | [译者序](https://pgint.vonng.com/preface2/) | [preface2.md](content/preface2.md) |
| | **第一部 · 基础架构与查询执行** | |
| 1 | [数据库集簇、数据库与数据表](https://pgint.vonng.com/ch1/) | [ch1.md](content/ch1.md) |
| 2 | [进程和内存架构](https://pgint.vonng.com/ch2/) | [ch2.md](content/ch2.md) |
| 3 | [查询处理](https://pgint.vonng.com/ch3/) | [ch3.md](content/ch3.md) |
| 4 | [外部数据包装器与并行查询](https://pgint.vonng.com/ch4/) | [ch4.md](content/ch4.md) |
| | **第二部 · 事务、存储与持久性** | |
| 5 | [并发控制](https://pgint.vonng.com/ch5/) | [ch5.md](content/ch5.md) |
| 6 | [清理过程](https://pgint.vonng.com/ch6/) | [ch6.md](content/ch6.md) |
| 7 | [堆内元组与仅索引扫描](https://pgint.vonng.com/ch7/) | [ch7.md](content/ch7.md) |
| 8 | [缓冲区管理器](https://pgint.vonng.com/ch8/) | [ch8.md](content/ch8.md) |
| 9 | [预写式日志](https://pgint.vonng.com/ch9/) | [ch9.md](content/ch9.md) |
| | **第三部 · 恢复、复制与高可用** | |
| 10 | [基础备份与时间点恢复](https://pgint.vonng.com/ch10/) | [ch10.md](content/ch10.md) |
| 11 | [流复制](https://pgint.vonng.com/ch11/) | [ch11.md](content/ch11.md) |
| 12 | [逻辑复制](https://pgint.vonng.com/ch12/) | [ch12.md](content/ch12.md) |
| | [技术附录](https://pgint.vonng.com/appendix/) | [appendix.md](content/appendix.md) |
| | [附录 · 许可与授权](https://pgint.vonng.com/license/) | [license.md](content/license.md) |

## 版本说明

中文译稿主体反映 PostgreSQL 9.x 至 11 前后的实现，保留了翻译当时的历史语境；本站另行补译英文原著后来加入的 TOAST、查询执行与基数估计、自动清理与并发 REPACK、AIO、WAL 汇总、增量备份、物理复制补充、第 12 章逻辑复制与技术附录，涉及 PostgreSQL 10 至 19。第 12 章在英文原著中仍标为 Beta/WIP，PostgreSQL 19相关内容也属于发布前描述。判断当前版本行为时，请同时参阅[持续更新的英文原著](https://www.interdb.jp/pg/)与 [PostgreSQL 官方文档](https://www.postgresql.org/docs/current/)。

## 许可与授权

英文原著版权归原作者 Hironobu Suzuki 所有；中文译本由译者保留所有权利（All Rights Reserved）。欢迎自由阅读并以链接形式分享；引用请注明原著与译本信息；整章转载、再分发或商业使用请先[联系译者](https://vonng.com/)取得授权。详见[许可与授权](https://pgint.vonng.com/license/)页面。

## 本地开发

本站基于 [Hugo](https://gohugo.io/) 与 [OINK](https://github.com/pgsty/oink) 主题构建，生产构建使用 `go.mod` 中固定的 OINK v1.1.0，需要 Hugo Extended 0.160.1+ 与 Go 1.27+。

| 命令 | 说明 |
|------|------|
| `make dev` | 本地开发服务器（通过 `HUGO_MODULE_REPLACEMENTS` 使用 `~/pgsty/oink` 本地工作树） |
| `make serve` | 以生产环境配置本地预览（使用 `go.mod` 固定版本） |
| `make build` | 生产构建，输出到 `public/` |
| `make check` | 生产构建 + 链接检查 + 站点检查 + OINK Book 契约检查 |
| `make check-local` | 同上，但针对本地 OINK 工作树 |

## 作者与译者

- **原著**：[Hironobu Suzuki（鈴木 啓修）](https://www.interdb.jp/)
- **翻译**：[冯若航](https://vonng.com/)、刘阳明、张文升（探探 PostgreSQL DBA Team，2018）

勘误与建议欢迎提交 [Issue](https://github.com/Vonng/pg-internal/issues)，或直接在各章节页面下方参与评论讨论。
