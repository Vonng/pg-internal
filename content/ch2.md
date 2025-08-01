---
title: 2. 进程和内存架构
weight: 102
breadcrumbs: false
---


本章总结了PostgreSQL中进程与内存的架构，有助于读者理解后续章节。 如果读者已经熟悉这些内容，可以直接跳过本章。

## 2.1 进程架构

PostgreSQL是一个客户端/服务器风格的关系型数据库管理系统，采用多进程架构，运行在单台主机上。

我们通常所谓的 “**PostgreSQL服务器（PostgreSQL Server）**” 实际上是一系列协同工作的进程集合，包含着下列进程：

* **Postgres服务器进程（Postgres Server Process）** 是所有数据库集簇管理进程的父进程。
* 每个 **后端进程（Backend Process）** 负责处理客户端发出的查询和语句。

* 各种 **后台进程（Background Process）** 负责执行各种数据库管理任务（例如清理过程与检查点过程）。

* 各种 **复制相关（Replication Associated Process）** 的进程负责流复制，流复制的细节会在[第11章](/ch11)中介绍。
* **后台工作进程（Background Worker Process）** 在 9.3 版被引入，它能执行任意由用户实现的处理逻辑。这里不详述，请参阅[官方文档](https://www.postgresql.org/docs/current/static/bgworker.html)。

以下几小节将详细描述前三种进程。

**图2.1 PostgreSQL的进程架构示例**

![](/img/fig-2-01.png)

> 本图展示了PostgreSQL服务器包含的进程：postgres服务器进程，两个后端进程，七个后台进程，以及两个客户端进程。 也画出了数据库集簇，共享内存，以及两个客户端。
>

### 2.1.1 Postgres服务器进程

如上所述，**Postgres服务器进程（postgres server process）** 是 PostgreSQL 服务器中所有进程的父进程，在早期版本中它被称为 *“postmaster“*。

带`start`参数执行[`pg_ctl`](https://www.postgresql.org/docs/current/static/app-pg-ctl.html)实用程序会启动一个postgres服务器进程。它会在内存中分配共享内存区域，启动各种后台进程，如有必要还会启动复制相关进程与后台工作进程，并等待来自客户端的连接请求。 每当接收到来自客户端的连接请求时，它都会启动一个后端进程 （然后由启动的后端进程处理该客户端发出的所有查询）。

一个postgres服务器进程只会监听一个网络端口，默认端口为5432。如果要在同一台主机上运行多个PostgreSQL服务器，则应为每个服务器配置不同的监听端口，如5432，5433等。

### 2.1.2 后端进程

每个后端进程（也称为*”postgres“*）由postgres服务器进程启动，并处理连接另一侧的客户端发出的所有查询。它通过单条TCP连接与客户端通信，并在客户端断开连接时终止。

因为一条连接只允许操作一个数据库，因此必须在连接到PostgreSQL服务器时显式指定要连接的数据库。

PostgreSQL允许多个客户端同时连接；配置参数[`max_connections`](https://www.postgresql.org/docs/current/static/runtime-config-connection.html#GUC-MAX-CONNECTIONS)用于控制最大客户端连接数（默认为100）。

因为PostgreSQL没有原生的连接池功能，因此如果许多客户端频繁地重复与PostgreSQL服务器建立断开连接（譬如WEB应用），则会导致建立连接与创建后端进程的开销变大。这种情况对数据库服务器的性能有负面影响，通常可以使用池化中间件（[pgbouncer](https://pgbouncer.github.io)或[pgpool-II](http://www.pgpool.net/mediawiki/index.php/Main_Page)）来避免该问题。



### 2.1.3 后台进程

表2.1是后台进程的列表。比起postgres服务器和后端进程，后台进程的种类要多很多。想要简单地解释每种后台进程的具体功能是不现实的，因为这些功能有赖PostgreSQL的内部机制与特定的独立特性。依赖于各个特定的特性以及PostgreSQL的内部机制。 因此在本章中仅做简要介绍。 细节将在后续章节中描述。

**表2.1 后台进程**

| 进程                       | 概述                                                         | 参考                         |
| -------------------------- | ------------------------------------------------------------ | ---------------------------- |
| background writer          | 本进程负责将共享缓冲池中的脏页逐渐刷入持久化存储中（例如，HDD，SSD）（在9.1及更旧版本中，它还负责处理**检查点（checkpoint）**） | [8.6](/ch8)                |
| checkpointer               | 在9.2及更新版本中，该进程负责处理检查点。                    | [8.6](/ch8), [9.7](/ch9) |
| autovacuum launcher        | 周期性地启动自动清理工作进程（更准确地说，它向Postgres服务器请求创建自动清理工作进程） | [6.5](/ch6)                |
| WAL writer                 | 本进程周期性地将WAL缓冲区中的WAL数据刷入持久存储中。         | [9.9](/ch9)                |
| statistics collector       | 本进程负责收集统计信息，用于诸如`pg_stat_activity`，`pg_stat_database`等系统视图。 |                              |
| logging collector (logger) | 本进程负责将错误消息写入日志文件。                           |                              |
| archiver                   | 本进程负责将日志归档。                                       | [9.10](/ch9)               |

>  这里展示了PostgreSQL服务器包含的实际进程。 在以下示例中有一个postgres服务器进程（pid为9687），两个后端进程（pid为9697和9717），以及表2.1中列出的几个后台进程正在运行，亦见图2.1。
>
> ```
> postgres> pstree -p 9687
> -+= 00001 root /sbin/launchd
>  \-+- 09687 postgres /usr/local/pgsql/bin/postgres -D /usr/local/pgsql/data
>    |--= 09688 postgres postgres: logger process     
>    |--= 09690 postgres postgres: checkpointer process     
>    |--= 09691 postgres postgres: writer process     
>    |--= 09692 postgres postgres: wal writer process     
>    |--= 09693 postgres postgres: autovacuum launcher process     
>    |--= 09694 postgres postgres: archiver process     
>    |--= 09695 postgres postgres: stats collector process     
>    |--= 09697 postgres postgres: postgres sampledb 192.168.1.100(54924) idle  
>    \--= 09717 postgres postgres: postgres sampledb 192.168.1.100(54964) idle in transaction  
> ```



## 2.2 内存架构

PostgreSQL的内存架构可以分为两部分：

+ 本地内存区域 —— 由每个后端进程分配，供自己使用。
+ 共享内存区域 —— 供PostgreSQL服务器的所有进程使用。

下面一小节简要介绍了这两部分架构。

**图2.2 PostgreSQL的内存架构**

![](/img/fig-2-02.png)

### 2.2.1 本地内存区域

每个后端进程都会分配一块本地内存区域用于查询处理。该区域会分为几个子区域 —— 子区域的大小有的固定，有的可变。 表2.2列出了主要的子区域。 详细信息将在后续章节中介绍。

**表2.2 本地内存区域**

| 子区域                 | 描述                                                         | 参考            |
| ---------------------- | ------------------------------------------------------------ | --------------- |
| `work_mem`             | 执行器在执行`ORDER BY`和`DISTINCT`时使用该区域对元组做排序，以及存储归并连接和散列连接中的连接表。 | [第3章](/ch3) |
| `maintenance_work_mem` | 某些类型的维护操作使用该区域（例如`VACUUM`，`REINDEX`）。    | [6.1](/ch6)   |
| `temp_buffers`         | 执行器使用此区域存储临时表。                                 |                 |

### 2.2.2 共享内存区域

PostgreSQL服务器启动时会分配共享内存区域。该区域分为几个固定大小的子区域。 表2.3列出了主要的子区域。 详细信息将在后续章节中介绍。

**表2.3 共享内存区域**

| 子区域               | 描述                                                         | 参考            |
| -------------------- | ------------------------------------------------------------ | --------------- |
| `shared buffer pool` | PostgreSQL将表和索引中的页面从持久存储加载至此，并直接操作它们。 | [第8章](/ch8) |
| `WAL buffer`         | 为确保服务故障不会导致任何数据丢失，PostgreSQL实现了WAL机制。 WAL数据（也称为XLOG记录）是PostgreSQL中的事务日志；WAL缓冲区是WAL数据在写入持久存储之前的缓冲区。 | [第9章](/ch9) |
| `commit log`         | **提交日志（Commit Log, CLOG）** 为并发控制（CC）机制保存了所需的所有事务状态（例如进行中，已提交，已中止等）。 | [5.4](/ch5)   |

除了上面这些，PostgreSQL还分配了这几个区域：

* 用于访问控制机制的子区域（例如信号量，轻量级锁，共享和排他锁等）。
* 各种后台进程使用的子区域，例如`checkpointer`和`autovacuum`。
* 用于事务处理的子区域，例如**保存点（save-point）** 与 **两阶段提交（2PC）**。

诸如此类。