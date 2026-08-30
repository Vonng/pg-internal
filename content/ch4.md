---
title: 外部数据包装器与并行查询
description: 外部数据包装器的查询路径，以及 PostgreSQL 并行扫描、并行连接与并行聚合机制。
search_keywords: [FDW, foreign data wrapper, postgres_fdw, 外部数据包装器, parallel query, parallel join, parallel aggregate, DSM, Gather, 并行查询]
type: book
book_kind: chapter
book_number: "4"
upstream_link: https://www.interdb.jp/pg/pgsql04/
weight: 140
breadcrumbs: false
---

本章将介绍两种相当实用，而且很有趣的特性：**外部数据包装器（Foreign Data Wrapper FDW）**与**并行查询（Parallel Query）**。

## 4.1 外部数据包装器（FDW）

2003年，SQL标准中添加了一个访问远程数据的规范，称为[SQL外部数据管理](https://wiki.postgresql.org/wiki/)（SQL/MED）。PostgreSQL在9.1版本开发出了FDW，实现了一部分SQL/MED中的特性。

在SQL/MED中，远程服务器上的表被称为**外部表（Foreign Table）**。 PostgreSQL的**外部数据包装器（FDW）** 使用与本地表类似的方式，通过SQL/MED来管理外部表。

{{< fig num="4.1" src="/img/fig-4-1.png" caption="FDW的基本概念" alt="Fig. 4.1. Basic concept of FDW." />}}

安装完必要的扩展并配置妥当后，就可以访问远程服务器上的外部表了。 例如假设有两个远程服务器分别名为`postgresql`和`mysql`，它们上面分别有两张表：`foreign_pg_tbl`和`foreign_my_tbl`。 在本例中，可以在本地服务器上执行`SELECT`查询以访问外部表，如下所示。

```sql
localdb=# -- foreign_pg_tbl 在远程postgresql服务器上
localdb-# SELECT count(*) FROM foreign_pg_tbl;
 count 
-------
 20000

localdb=# -- foreign_my_tbl 在远程mysql服务器上
localdb-# SELECT count(*) FROM foreign_my_tbl;
 count 
-------
 10000
```

此外还可以在本地连接来自不同服务器中的外部表。

```sql
localdb=# SELECT count(*) FROM foreign_pg_tbl AS p, foreign_my_tbl AS m WHERE p.id = m.id;
 count 
-------
 10000
```

[Postgres wiki](https://wiki.postgresql.org/wiki/Foreign_data_wrappers)中列出了很多现有的FDW扩展。但只有[`postgres_fdw`](https://www.postgresql.org/docs/current/postgres-fdw.html) 与[`file_fdw`](https://www.postgresql.org/docs/current/file-fdw.html) 是由官方PostgreSQL全球开发组维护的。`postgres_fdw`可用于访问远程PostgreSQL服务器。

以下部分将详细介绍PostgreSQL的FDW。 4.1.1节为概述，4.1.2节介绍了`postgres_fdw`扩展的工作方式。

> #### Citus
>
> [Citus](https://github.com/citusdata/citus)是由[citusdata.com](https://www.citusdata.com)开发的开源PostgreSQL扩展，它能创建用于并行化查询的分布式PostgreSQL服务器集群。citus算是PostgreSQL生态中机制上最为复杂，且商业上最为成功的扩展之一，它也是一种FDW。



### 4.1.1 概述

使用FDW特性需要先安装相应的扩展，并执行一些设置命令，例如[`CREATE FOREIGN TABLE`](https://www.postgresql.org/docs/current/sql-createforeigntable.html)，[`CREATE SERVER`](https://www.postgresql.org/docs/current/sql-createserver.html) 和[`CREATE USER MAPPING`](https://www.postgresql.org/docs/current/sql-createusermapping.html)（细节请参阅[官方文档](https://www.postgresql.org/docs/9.5/postgres-fdw.html#AEN180314)）。

在配置妥当之后，查询处理期间，执行器将会调用扩展中定义的相应函数来访问外部表。



{{< fig num="4.2" src="/img/fig-4-2.png" caption="FDW是如何执行的" alt="Fig. 4.2. How FDWs perform." />}}

1. 分析器为输入的SQL创建一棵查询树。
2. 计划器（或执行器）连接到远程服务器。
3. 如果启用了[`use_remote_estimate`](https://www.postgresql.org/docs/current/postgres-fdw.html#id-1.11.7.43.10.4)选项（默认关闭），则计划器将执行`EXPLAIN`命令以估计每条计划路径的代价。
4. 计划器按照计划树创建出纯文本SQL语句，在内部称该过程为**逆解析（deparesing）**。
5. 执行器将纯文本SQL语句发送到远程服务器并接收结果。

如有必要，执行器会进一步处理接收到的结果。 例如执行多表查询时，执行器会将收到的数据与其他表进行连接。

以下各节介绍了每一步中的具体细节。

#### 4.1.1.1 创建一棵查询树

分析器会根据输入的SQL创建一棵查询树，并使用外部表的定义。当执行命令[`CREATE FOREIGN TABLE`](https://www.postgresql.org/docs/current/sql-createforeigntable.html) 和[`IMPORT FOREIGN SCHEMA`](https://www.postgresql.org/docs/current/sql-importforeignschema.html)时，外部表的定义会被存储至系统目录[`pg_catalog.pg_class`](https://www.postgresql.org/docs/current/catalog-pg-class.html)和[`pg_catalog.pg_foreign_table`](https://www.postgresql.org/docs/current/catalog-pg-foreign-table.html)中。

#### 4.1.1.2 连接至远程服务器

计划器（或执行器）会使用特定的库连接至远程数据库服务器。 例如要连接至远程PostgreSQL服务器时，`postgres_fdw`会使用[`libpq`](https://www.postgresql.org/docs/current/libpq.html)。 而连接到mysql服务器时，由EnterpriseDB开发的[`mysql_fdw`](https://github.com/EnterpriseDB/mysql_fdw)使用`libmysqlclient`。

当执行[`CREATE USER MAPPING`](https://www.postgresql.org/docs/current/sql-createusermapping.html)和[`CREATE SERVER`](https://www.postgresql.org/docs/current/sql-createserver.html)命令时，诸如用户名，服务器IP地址和端口号等连接参数会被存储至系统目录[`pg_catalog.pg_user_mapping`](https://www.postgresql.org/docs/current/catalog-pg-user-mapping.html)和[`pg_catalog.pg_foreign_server`](https://www.postgresql.org/docs/current/catalog-pg-foreign-server.html)中。

#### 4.1.1.3 使用EXPLAIN命令创建计划树（可选）

PostgreSQL的FDW机制支持一种特性：获取外部表上的统计信息，用于估计查询代价。一些FDW扩展使用了该特性，例如`postgres_fdw`，`mysql_fdw`，`tds_fdw`和`jdbc2_fdw`。

如果使用[`ALTER SERVER`](https://www.postgresql.org/docs/current/sql-alterserver.html)命令将`use_remote_estimate`选项设置为`on`，则计划器会向远程服务器发起查询，执行`EXPLAIN`命令获取执行计划的代价。否则在默认情况下，会使用默认内置常量值作为代价。

```sql
localdb=# ALTER SERVER remote_server_name OPTIONS (use_remote_estimate 'on');
```

尽管一些扩展也会执行`EXPLAIN`命令，但目前只有`postgres_fdw`才能忠于`EXPLAIN`命令的真正意图，因为PostgreSQL的`EXPLAIN`命令会同时返回启动代价和总代价。而其他DBMS的FDW扩展一般无法使用`EXPLAIN`命令的结果进行规划。 例如MySQL的`EXPLAIN`命令仅仅返回估计的行数， 但如[第3章](/ch3/)所述，PostgreSQL的计划器需要更多的信息来估算代价。

#### 4.1.1.4 逆解析

在生成执行计划树的过程中，计划器会为执行计划树上外部表的扫描路径创建相应的纯文本SQL语句。 例如{{< xref fig="4.3" anchor="fig-4.3" >}}图4.3{{< /xref >}}展示了下列`SELECT`语句对应的计划树。

```sql
localdb=# SELECT * FROM tbl_a AS a WHERE a.id < 10;
```

{{< xref fig="4.3" anchor="fig-4.3" >}}图4.3{{< /xref >}}展示了一个存储着纯文本形式`SELECT`语句的`ForeignScan`节点，`PlannedStmt`是执行计划树对应的数据结构，包含指向`ForeignScan`节点的链接。 这里，`postgres_fdw`从查询树中重新创建出`SELECT`纯文本语句，该过程在PostgreSQL中被称为**逆解析（deparsing）**。

{{< fig num="4.3" src="/img/fig-4-3.png" caption="扫描外部表的计划树样例" alt="Fig. 4.3. Example of the plan tree that scans a foreign table." />}}

使用`mysql_fdw`时，则会从查询树中重新创建MySQL相应的`SELECT`语句。 使用[`redis_fdw`](https://github.com/pg-redis-fdw/redis_fdw)或[`rw_redis_fdw`](https://github.com/nahanni/rw_redis_fdw)会创建一条Redis中的[`SELECT`命令](https://redis.io/commands/select)。

#### 4.1.1.5 发送SQL命令并接收结果

在进行逆解析之后，执行器将逆解析得到的SQL语句发送到远程服务器并接收结果。

扩展的开发者决定了将SQL语句发送至远程服务器的具体方法。 例如`mysql_fdw`在发送多条SQL语句时不使用事务。 在`mysql_fdw`中执行`SELECT`查询的典型SQL语句序列如下所示（{{< xref fig="4.4" anchor="fig-4.4" >}}图4.4{{< /xref >}}）。

* （5-1）将`SQL_MODE`设置为`'ANSI_QUOTES'`。
* （5-2）将`SELECT`语句发送到远程服务器。
* （5-3）从远程服务器接收结果。这里`mysql_fdw`会将结果转换为PostgreSQL可读的格式。所有FDW扩展都实现了将结果转换为PostgreSQL可读数据的功能。

{{< fig num="4.4" src="/img/fig-4-4.png" caption="`mysql_fdw`执行一个典型SELECT查询时的SQL语句序列" alt="Fig. 4.4. Typical sequence of SQL statements to execute a SELECT query in mysql_fdw" />}}

下面是远程服务器的日志，列出了实际接收到的语句。

```sql
mysql> SELECT command_type,argument FROM mysql.general_log;
+--------------+-----------------------------------------------------------+
| command_type | argument                                                              |
+--------------+-----------------------------------------------------------+
... snip ...

| Query        | SET sql_mode='ANSI_QUOTES'                                            |
| Prepare      | SELECT `id`, `data` FROM `localdb`.`tbl_a` WHERE ((`id` < 10))         |
| Close stmt   |                                                                       |
+--------------+-----------------------------------------------------------+
```

`postgres_fdw`中的SQL命令顺序要更为复杂。在`postgres_fdw`中执行一个典型的`SELECT`查询，实际的语句序列如{{< xref fig="4.5" anchor="fig-4.5" >}}图4.5{{< /xref >}}所示。

* （5-1）启动远程事务。远程事务的默认隔离级别是`REPEATABLE READ`；但如果本地事务的隔离级别设置为`SERIALIZABLE`，则远程事务的隔离级别也会设置为`SERIALIZABLE`。

* （5-2）-（5-4）声明一个游标，SQL语句基本上以游标的方式来执行。

* （5-5）执行`FETCH`命令获取结果。默认情况下`FETCH`命令一次获取100行。

* （5-6）从远程服务器接收结果。

* （5-7）关闭游标。

* （5-8）提交远程事务。

{{< fig num="4.5" src="/img/fig-4-5.png" caption="`postgres_fdw`执行一个典型SELECT查询时的SQL语句序列" alt="Fig. 4.5. Typical sequence of SQL statements to execute a SELECT query in postgres_fdw." />}}

这里是远程服务器的实际日志。

```
LOG:  statement: START TRANSACTION ISOLATION LEVEL REPEATABLE READ
LOG:  parse : DECLARE c1 CURSOR FOR SELECT id, data FROM public.tbl_a WHERE ((id < 10))
LOG:  bind : DECLARE c1 CURSOR FOR SELECT id, data FROM public.tbl_a WHERE ((id < 10))
LOG:  execute : DECLARE c1 CURSOR FOR SELECT id, data FROM public.tbl_a WHERE ((id < 10))
LOG:  statement: FETCH 100 FROM c1
LOG:  statement: CLOSE c1
LOG:  statement: COMMIT TRANSACTION
```

> #### `postgres_fdw`中远程事务的默认隔离级别
>
> 远程事务的默认隔离级别为`REPEATABLE READ`，官方文档给出了原因和说明：
>
> > 当本地事务使用`SERIALIZABLE`隔离级别时，远程事务也会使用`SERIALIZABLE`隔离级别，否则使用`REPEATABLE READ`隔离级别。 这样做可以确保在远程服务器上执行多次扫表时，每次的结果之间都能保持一致。因此，即使其他活动在远程服务器上进行了并发更新，单个事务中的连续查询也将看到远程服务器上的一致性快照。



### 4.1.2 `postgres_fdw`的工作原理

`postgres_fdw`扩展是一个由PostgreSQL全球开发组官方维护的特殊模块，其源码包含在PostgreSQL源码树中。

`postgres_fdw`正处于不断改善的过程中。 {{< xref tbl="4.1" anchor="tbl-4.1" >}}表4.1{{< /xref >}}列出了官方文档中与`postgres_fdw`有关的发行说明。

{{< tbl num="4.1" caption="与postgres_fdw有关的发布说明（摘自官方文档）" >}}
| 版本 | 描述                                                         |
| ---- | ------------------------------------------------------------ |
| 9.3  | `postgres_fdw`模块正式发布                                   |
| 9.6  | 在远程服务器上执行排序<br />在远程服务器上执行连接<br />如果可行，在远程服务器上执行`UPDATE`与`DELETE`<br />允许在服务器与表的选项中设置批量拉取结果集的大小 |
| 10   | 如果可行， 将聚合函数下推至远程服务器                        |
{{< /tbl >}}
前一节描述了`postgres_fdw`如何处理单表查询，接下来的小节将介绍`postgres_fdw`如何处理多表查询，排序操作与聚合函数。

本小节重点介绍`SELECT`语句；但`postgres_fdw`还可以处理其他DML（`INSERT`，`UPDATE`和`DELETE`）语句。

> #### PostgreSQL的FDW不会检测死锁
>
> `postgres_fdw`与FDW功能并不支持分布式锁管理器与分布式死锁检测功能， 因此很容易产生死锁。 例如某客户端A更新了一个本地表`tbl_local`与一个外部表`tbl_remote`，而另一个客户端B以相反的顺序更新`tbl_remote`和`tbl_local`，则这两个事务陷入死锁。但PostgreSQL无法检测到这种情况， 因而无法提交这些事务。
>
> ```sql
> localdb=# -- Client A
> localdb=# BEGIN;
> BEGIN
> localdb=# UPDATE tbl_local SET data = 0 WHERE id = 1;
> UPDATE 1
> localdb=# UPDATE tbl_remote SET data = 0 WHERE id = 1;
> UPDATE 1
> ```
>
> ```sql
> localdb=# -- Client B
> localdb=# BEGIN;
> BEGIN
> localdb=# UPDATE tbl_remote SET data = 0 WHERE id = 1;
> UPDATE 1
> localdb=# UPDATE tbl_local SET data = 0 WHERE id = 1;
> UPDATE 1
> ```

#### 4.1.2.1 多表查询

当执行多表查询时，`postgres_fdw`使用单表`SELECT`语句依次拉取每个外部表，并在本地服务器上执行连接操作。

在9.5或更早版本中，即使所有外部表都存储在同一个远程服务器中，`postgres_fdw`也会单独拉取每个表再连接。

在9.6或更高版本中，`postgres_fdw`已经有所改进，当外部表位于同一服务器上且[`use_remote_estimate`](https://www.postgresql.org/docs/current/postgres-fdw.html)选项打开时，可以在远程服务器上执行远程连接操作。

执行细节如下所述。

**9.5及更早版本：**

我们研究一下PostgreSQL如何处理以下查询：两个外部表的连接：`tbl_a`和`tbl_b`。

```sql
localdb=# SELECT * FROM tbl_a AS a, tbl_b AS b WHERE a.id = b.id AND a.id < 200;
```

`EXPLAIN`的执行结果如下

```sql
localdb=# EXPLAIN SELECT * FROM tbl_a AS a, tbl_b AS b WHERE a.id = b.id AND a.id < 200;
                                  QUERY PLAN                                  
------------------------------------------------------------------------------
 Merge Join  (cost=532.31..700.34 rows=10918 width=16)
   Merge Cond: (a.id = b.id)
   ->  Sort  (cost=200.59..202.72 rows=853 width=8)
         Sort Key: a.id
         ->  Foreign Scan on tbl_a a  (cost=100.00..159.06 rows=853 width=8)
   ->  Sort  (cost=331.72..338.12 rows=2560 width=8)
         Sort Key: b.id
         ->  Foreign Scan on tbl_b b  (cost=100.00..186.80 rows=2560 width=8)
(8 rows)
```

结果显示，执行器选择了归并连接，并按以下步骤处理：

* 第8行：执行器使用外部表扫描拉取表`tbl_a`。

* 第6行：执行器在本地服务器上对拉取的`tbl_a`行进行排序。

* 第11行：执行器使用外表扫描拉取表`tbl_b`。

* 第9行：执行器在本地服务器上对拉取的`tbl_b`行进行排序。

* 第4行：执行器在本地服务器上执行归并连接操作。

下面描述执行器如何拉取行集（{{< xref fig="4.6" anchor="fig-4.6" >}}图4.6{{< /xref >}}）。

* （5-1）启动远程事务。

* （5-2）声明游标`c1`，其`SELECT`语句如下所示：

  ```sql
  SELECT id，data FROM public.tbl_a WHERE（id <200）
  ```

* （5-3）执行`FETCH`命令以拉取游标`c1`的结果。

* （5-4）声明游标`c2`，其`SELECT`语句如下所示：

  ```sql
  SELECT id,data FROM public.tbl_b
  ```

  注意原来双表查询中的WHERE子句是`tbl_a.id = tbl_b.id AND tbl_a.id <200`；因而从逻辑上讲这条`SELECT`语句也可以添加上一条WHERE子句`tbl_b.id <200`。但`postgres_fdw`没有办法执行这样的推理，因此执行器必须执行不包含任何WHERE子句的`SELECT`语句，获取外部表`tbl_b` 中的所有行。

  这种处理方式效率很差，因为必须通过网络从远程服务器读取不必要的行。此外，执行归并连接还需要先对接受到的行进行排序。

* （5-5）执行`FETCH`命令，拉取游标`c2`的结果。

* （5-6）关闭游标`c1`。

* （5-7）关闭游标`c2`。

* （5-8）提交事务。

{{< fig num="4.6" src="/img/fig-4-6.png" caption="在9.5及更早版本中执行多表查询时的SQL语句序列" alt="Fig. 4.6. Sequence of SQL statements to execute the Multi-Table Query in version 9.5 or earlier." />}}

这里是远程服务器的实际日志。

```
LOG:  statement: START TRANSACTION ISOLATION LEVEL REPEATABLE READ
LOG:  parse : DECLARE c1 CURSOR FOR
      SELECT id, data FROM public.tbl_a WHERE ((id < 200))
LOG:  bind : DECLARE c1 CURSOR FOR
      SELECT id, data FROM public.tbl_a WHERE ((id < 200))
LOG:  execute : DECLARE c1 CURSOR FOR
      SELECT id, data FROM public.tbl_a WHERE ((id < 200))
LOG:  statement: FETCH 100 FROM c1
LOG:  statement: FETCH 100 FROM c1
LOG:  parse : DECLARE c2 CURSOR FOR
      SELECT id, data FROM public.tbl_b
LOG:  bind : DECLARE c2 CURSOR FOR
      SELECT id, data FROM public.tbl_b
LOG:  execute : DECLARE c2 CURSOR FOR
      SELECT id, data FROM public.tbl_b
LOG:  statement: FETCH 100 FROM c2
LOG:  statement: FETCH 100 FROM c2
LOG:  statement: FETCH 100 FROM c2
LOG:  statement: FETCH 100 FROM c2

... snip

LOG:  statement: FETCH 100 FROM c2
LOG:  statement: FETCH 100 FROM c2
LOG:  statement: FETCH 100 FROM c2
LOG:  statement: FETCH 100 FROM c2
LOG:  statement: CLOSE c2
LOG:  statement: CLOSE c1
LOG:  statement: COMMIT TRANSACTION
```

在接收到行之后，执行器对接收到的`tbl_a`和`tbl_b`行进行排序，然后对已排序的行执行合并连接操作。

**9.6或更高版本：**

如果启用了`use_remote_estimate`选项（默认为关闭），则`postgres_fdw`会发送几条`EXPLAIN`命令，用于获取与外部表相关的所有计划的代价。

当发送`EXPLAIN`命令时，`postgres_fdw`将为每个单表查询执行`EXPLAIN`，也为执行远程连接操作时的`SELECT`语句执行`EXPLAIN` 。在本例中，以下七个`EXPLAIN`命令会被发送至远程服务器，用于估算每个`SELECT`语句的开销，从而选择开销最小的执行计划。

```sql
(1) EXPLAIN SELECT id, data FROM public.tbl_a WHERE ((id < 200))
(2) EXPLAIN SELECT id, data FROM public.tbl_b
(3) EXPLAIN SELECT id, data FROM public.tbl_a WHERE ((id < 200)) ORDER BY id ASC NULLS LAST
(4) EXPLAIN SELECT id, data FROM public.tbl_a WHERE ((((SELECT null::integer)::integer) = id)) AND ((id < 200))
(5) EXPLAIN SELECT id, data FROM public.tbl_b ORDER BY id ASC NULLS LAST
(6) EXPLAIN SELECT id, data FROM public.tbl_b WHERE ((((SELECT null::integer)::integer) = id))
(7) EXPLAIN SELECT r1.id, r1.data, r2.id, r2.data FROM (public.tbl_a r1 INNER JOIN public.tbl_b r2 ON (((r1.id = r2.id)) AND ((r1.id < 200))))
```

让我们在本地服务器上执行`EXPLAIN`命令，并观察计划器选择了哪一个计划。

```sql
localdb=# EXPLAIN SELECT * FROM tbl_a AS a, tbl_b AS b WHERE a.id = b.id AND a.id < 200;
                        QUERY PLAN                         
-----------------------------------------------------------
 Foreign Scan  (cost=134.35..244.45 rows=80 width=16)
   Relations: (public.tbl_a a) INNER JOIN (public.tbl_b b)
(2 rows)
```

结果显示，计划器选择了在远程服务器上进行`INNER JOIN`处理的执行计划，也是最有效率的执行计划。

下面讲述`postgres_fdw`是如何执行这一过程的，如{{< xref fig="4.7" anchor="fig-4.7" >}}图4.7{{< /xref >}}所示。



{{< fig num="4.7" src="/img/fig-4-7.png" caption="执行远程连接操作时的SQL语句序列，9.6及更高版本" alt="Fig. 4.7. Sequence of SQL statements to execute the remote-join operation in version 9.6 or later." />}}



* （3-1）启动远程事务。

* （3-2）执行`EXPLAIN`命令，估计每条计划路径的代价。在本例中执行了七条`EXPLAIN`命令。然后计划器根据`EXPLAIN`命令的结果，选取具有最低开销的`SELECT`查询。

* （5-1）声明游标`c1`，其`SELECT`语句如下所示：

  ```sql
  SELECT r1.id, r1.data, r2.id, r2.data 
  FROM (public.tbl_a r1 INNER JOIN public.tbl_b r2 
    ON (((r1.id = r2.id)) AND ((r1.id < 200))))
  ```

* （5-2）从远程服务器接收结果。

* （5-3）关闭游标`c1`。

* （5-4）提交事务。

这里是远程服务器的实际日志。

```sql
LOG:  statement: START TRANSACTION ISOLATION LEVEL REPEATABLE READ
LOG:  statement: EXPLAIN SELECT id, data FROM public.tbl_a WHERE ((id < 200))
LOG:  statement: EXPLAIN SELECT id, data FROM public.tbl_b
LOG:  statement: EXPLAIN SELECT id, data FROM public.tbl_a WHERE ((id < 200)) ORDER BY id ASC NULLS LAST
LOG:  statement: EXPLAIN SELECT id, data FROM public.tbl_a WHERE ((((SELECT null::integer)::integer) = id)) AND ((id < 200))
LOG:  statement: EXPLAIN SELECT id, data FROM public.tbl_b ORDER BY id ASC NULLS LAST
LOG:  statement: EXPLAIN SELECT id, data FROM public.tbl_b WHERE ((((SELECT null::integer)::integer) = id))
LOG:  statement: EXPLAIN SELECT r1.id, r1.data, r2.id, r2.data FROM (public.tbl_a r1 INNER JOIN public.tbl_b r2 ON (((r1.id = r2.id)) AND ((r1.id < 200))))
LOG:  parse: DECLARE c1 CURSOR FOR
	   SELECT r1.id, r1.data, r2.id, r2.data FROM (public.tbl_a r1 INNER JOIN public.tbl_b r2 ON (((r1.id = r2.id)) AND ((r1.id < 200))))
LOG:  bind: DECLARE c1 CURSOR FOR
	   SELECT r1.id, r1.data, r2.id, r2.data FROM (public.tbl_a r1 INNER JOIN public.tbl_b r2 ON (((r1.id = r2.id)) AND ((r1.id < 200))))
LOG:  execute: DECLARE c1 CURSOR FOR
	   SELECT r1.id, r1.data, r2.id, r2.data FROM (public.tbl_a r1 INNER JOIN public.tbl_b r2 ON (((r1.id = r2.id)) AND ((r1.id < 200))))
LOG:  statement: FETCH 100 FROM c1
LOG:  statement: FETCH 100 FROM c1
LOG:  statement: CLOSE c1
LOG:  statement: COMMIT TRANSACTION
```

注意如果禁用`use_remote_estimate`选项（默认情况），则远程连接查询很少会被选择，因为这种情况下其代价会使用一个很大的预置值进行估计。

#### 4.1.2.2 排序操作

在9.5或更早版本中，排序操作（如`ORDER BY`）都是在本地服务器上处理的。即，本地服务器在排序操作之前从远程服务器拉取所有的目标行。让我们通过`EXPLAIN`来看一个包含`ORDER BY`子句的简单查询是如何被处理的。

```sql
localdb=# EXPLAIN SELECT * FROM tbl_a AS a WHERE a.id < 200 ORDER BY a.id;
                              QUERY PLAN                               
-----------------------------------------------------------------------
 Sort  (cost=200.59..202.72 rows=853 width=8)
   Sort Key: id
   ->  Foreign Scan on tbl_a a  (cost=100.00..159.06 rows=853 width=8)
(3 rows)
```

第6行：执行器将以下查询发送到远程服务器，然后获取查询结果。

```
SELECT id, data FROM public.tbl_a WHERE ((id < 200))
```

第4行：执行器在本地服务器上对拉取的`tbl_a`中的行进行排序。

这里是远程服务器的实际日志。

```
LOG:  statement: START TRANSACTION ISOLATION LEVEL REPEATABLE READ
LOG:  parse : DECLARE c1 CURSOR FOR
      SELECT id, data FROM public.tbl_a WHERE ((id < 200))
LOG:  bind : DECLARE c1 CURSOR FOR
      SELECT id, data FROM public.tbl_a WHERE ((id < 200))
LOG:  execute : DECLARE c1 CURSOR FOR
      SELECT id, data FROM public.tbl_a WHERE ((id < 200))
LOG:  statement: FETCH 100 FROM c1
LOG:  statement: FETCH 100 FROM c1
LOG:  statement: CLOSE c1
LOG:  statement: COMMIT TRANSACTION
```

在9.6或更高版本中，如果可行，`postgres_fdw`能在远程服务器上直接执行带`ORDER BY`子句的`SELECT`语句。

```sql
localdb=# EXPLAIN SELECT * FROM tbl_a AS a WHERE a.id < 200 ORDER BY a.id;
                           QUERY PLAN                            
-----------------------------------------------------------------
 Foreign Scan on tbl_a a  (cost=100.00..167.46 rows=853 width=8)
(1 row)
```

第4行：执行器将以下带`ORDER BY`子句的查询发送至远程服务器，然后拉取已排序的查询结果。

```sql
SELECT id, data FROM public.tbl_a WHERE ((id < 200)) ORDER BY id ASC NULLS LAST
```

这里是远程服务器的实际日志。

```sql
LOG:  statement: START TRANSACTION ISOLATION LEVEL REPEATABLE READ
LOG:  parse : DECLARE c1 CURSOR FOR
	   SELECT id, data FROM public.tbl_a WHERE ((id < 200)) ORDER BY id ASC NULLS LAST
LOG:  bind : DECLARE c1 CURSOR FOR
	   SELECT id, data FROM public.tbl_a WHERE ((id < 200)) ORDER BY id ASC NULLS LAST
LOG:  execute : DECLARE c1 CURSOR FOR
	   SELECT id, data FROM public.tbl_a WHERE ((id < 200)) ORDER BY id ASC NULLS LAST
LOG:  statement: FETCH 100 FROM c1
LOG:  statement: FETCH 100 FROM c1
LOG:  statement: CLOSE c1
LOG:  statement: COMMIT TRANSACTION
```

#### 4.1.2.3 聚合函数

在9.6及更早版本中，类似于前一小节中提到的排序操作，`AVG()`和`COUNT()`这样的聚合函数会在本地服务器上进行处理，如下所示。

```sql
localdb=# EXPLAIN SELECT AVG(data) FROM tbl_a AS a WHERE a.id < 200;
                              QUERY PLAN                               
-----------------------------------------------------------------------
 Aggregate  (cost=168.50..168.51 rows=1 width=4)
   ->  Foreign Scan on tbl_a a  (cost=100.00..166.06 rows=975 width=4)
(2 rows)
```

第5行：执行器将以下查询发送到远程服务器，然后拉取查询结果。

```sql
SELECT id, data FROM public.tbl_a WHERE ((id < 200))
```

第4行：执行器在本地服务器上对拉取的`tbl_a`行集求均值。

这一过程开销很大，因为发送大量的行会产生大量网络流量，而且需要很长时间。

这里是远程服务器的实际日志。

```sql
LOG:  statement: START TRANSACTION ISOLATION LEVEL REPEATABLE READ
LOG:  parse : DECLARE c1 CURSOR FOR
      SELECT data FROM public.tbl_a WHERE ((id < 200))
LOG:  bind : DECLARE c1 CURSOR FOR
      SELECT data FROM public.tbl_a WHERE ((id < 200))
LOG:  execute : DECLARE c1 CURSOR FOR
      SELECT data FROM public.tbl_a WHERE ((id < 200))
LOG:  statement: FETCH 100 FROM c1
LOG:  statement: FETCH 100 FROM c1
LOG:  statement: CLOSE c1
LOG:  statement: COMMIT TRANSACTION
```

在10或更高版本中，如果可行的话，`postgres_fdw`将在远程服务器上执行带聚合函数的`SELECT`语句。

```sql
localdb=# EXPLAIN SELECT AVG(data) FROM tbl_a AS a WHERE a.id < 200;
                     QUERY PLAN                      
-----------------------------------------------------
 Foreign Scan  (cost=102.44..149.03 rows=1 width=32)
   Relations: Aggregate on (public.tbl_a a)
(2 rows)
```

第4行：执行器将以下包含`AVG()`函数的查询发送至远程服务器，然后获取查询结果。

```sql
SELECT avg(data) FROM public.tbl_a WHERE ((id < 200))
```

这种处理方式显然更为高效，因为远程服务器会负责计算均值，仅发送单行结果。

这里是远程服务器的实际日志。

```
LOG:  statement: START TRANSACTION ISOLATION LEVEL REPEATABLE READ
LOG:  parse : DECLARE c1 CURSOR FOR
	   SELECT avg(data) FROM public.tbl_a WHERE ((id < 200))
LOG:  bind : DECLARE c1 CURSOR FOR
	   SELECT avg(data) FROM public.tbl_a WHERE ((id < 200))
LOG:  execute : DECLARE c1 CURSOR FOR
	   SELECT avg(data) FROM public.tbl_a WHERE ((id < 200))
LOG:  statement: FETCH 100 FROM c1
LOG:  statement: CLOSE c1
LOG:  statement: COMMIT TRANSACTION
```

> #### 下推
>
> 与上面的例子类似，**下推（push-down）** 指的是本地服务器允许一些操作在远程服务器上执行，例如聚合函数。



## 4.2 并行查询

> **编号说明：** 英文原著当前把并行查询编为[第3.7节](https://www.interdb.jp/pg/pgsql03/07.html)；中文译稿保留原有第4.2节位置，以免破坏既有章节链接与图号。

[并行查询](https://www.postgresql.org/docs/current/parallel-query.html)由PostgreSQL 9.6（2016年）引入，使用多个后台worker共同处理一条查询。满足并行条件时，执行查询的postgres进程充当**leader**，最多启动`max_parallel_workers_per_gather`指定数量的worker。每个worker完成一部分扫描，把结果返回leader汇总。

{{< fig num="4.8" src="/img/fig-4-08.png" caption="并行查询的基本概念" alt="图4.8 两个worker并行扫描数据块并把结果返回leader" />}}

PostgreSQL 11起，默认开启的`parallel_leader_participation`允许leader在等待worker结果时也参与执行。为简化下文图示，假设该参数关闭，leader只负责协调与汇总。

并行查询能力持续演进：9.6支持并行顺序扫描、嵌套循环与哈希连接；10加入归并连接、B-tree索引扫描和位图堆扫描；11加入共享哈希表的并行哈希连接、并行建索引和更多并行DDL；后续版本继续扩展外表、`DISTINCT`及外连接等场景。

> 并行查询主要面向读取型工作负载，目前不支持游标操作。

### 4.2.1 并行查询概要

{{< fig num="4.9" src="/img/fig-4-09.png" caption="并行查询的执行流程" alt="图4.9 leader创建计划和DSM，启动worker扫描并通过TupleQueue收集结果" />}}

处理流程如下：

1. **leader创建计划：** 优化器生成包含可并行执行节点的计划树。
2. **保存共享信息：** leader把计划树、会话状态等必要信息写入动态共享内存（DSM）。
3. **创建worker：** leader启动后台worker。
4. **worker初始化：** 各worker读取DSM，建立与leader一致的执行环境。
5. **扫描并返回结果：** worker主动领取数据块，调用`SeqNext()`、`IndexNext()`等函数扫描，并返回结果。
6. **leader汇总：** leader收集所有worker结果。
7. **清理：** 查询结束后终止worker并释放DSM。

下面的示例使用一张包含100万行的表：

```sql
CREATE TABLE d (id double precision, data int);
INSERT INTO d
SELECT i::double precision, (random() * 1000)::int
  FROM generate_series(1, 1000000) AS i;
ANALYZE d;
```

#### 4.2.1.1 创建并行计划

优化器并不总会考虑并行执行。表大小通常需要达到`min_parallel_table_scan_size`（默认8 MB），索引则需达到`min_parallel_index_scan_size`（默认512 kB）。最简单的计划如下：

```sql
testdb=# EXPLAIN SELECT * FROM d WHERE id BETWEEN 1 AND 100;
                                     QUERY PLAN
-------------------------------------------------------------------------------------
 Gather  (cost=1000.00..16609.10 rows=1 width=12)
   Workers Planned: 2
   ->  Parallel Seq Scan on d  (cost=0.00..15609.00 rows=1 width=12)
         Filter: ((id >= '1'::double precision) AND (id <= '100'::double precision))
(4 rows)
```

{{< fig num="4.10" src="/img/fig-4-10.png" caption="leader的并行计划树" alt="图4.10 Gather节点位于Parallel SeqScan之上" />}}

`Gather`收集worker结果；其他并行专用节点还包括保持排序的`Gather Merge`、并行扫描分区或`UNION ALL`的`Parallel Append`，以及并行聚合使用的`Partial Aggregate`和`Finalize Aggregate`。

只有`parallel_safe=true`的节点才能进入`Gather`以下、由worker执行的子计划。

#### 4.2.1.2 保存共享信息

leader把两类信息写入DSM：

* **执行状态：** GUC参数、事务快照、当前子事务ID、动态加载的库等，由`InitializeParallelDSM()`保存。
* **查询信息：** `PlannedStmt`、`ParamListInfo`，并行扫描描述符、统计与资源使用结构等，由`ExecInitParallelPlan()`初始化。

leader还会在DSM中建立`TupleQueue`，作为读取worker结果的通信通道。

#### 4.2.1.3 创建worker

计划中的worker数不一定等于实际启动数。全局`max_parallel_workers`会限制系统并行worker总数，其他并行查询也可能已经占用槽位。`EXPLAIN ANALYZE`同时显示`Workers Planned`与`Workers Launched`，可用于核对实际资源分配。

#### 4.2.1.4 初始化worker

worker启动后从DSM读取执行状态和查询信息，使GUC、快照和动态库与leader一致。随后根据共享的`PlannedStmt`重建只包含`parallel_safe`节点的子计划，通常就是leader计划中`Gather`以下的部分。

{{< fig num="4.11" src="/img/fig-4-11.png" caption="worker从leader计划重建子计划" alt="图4.11 worker只重建Gather以下可并行执行的计划节点" />}}

#### 4.2.1.5 扫描与返回结果

执行器对元组访问进行了高度抽象，这一点同样适用于并行查询。leader与worker共享DSM中的执行环境，因此多个进程可以通过`SeqNext()`按需领取不同数据块并执行同一次顺序扫描；结果通过DSM中的`TupleQueue`返回`Gather`。

#### 4.2.1.6 汇总结果

`Gather`负责读取各worker的TupleQueue并向上层节点输出；需要保持全局排序时使用`Gather Merge`。

### 4.2.2 并行连接

PostgreSQL支持并行嵌套循环、归并与哈希连接。下面使用表`d`和`f`：

```sql
CREATE INDEX d_id_idx ON d (id);
CREATE TABLE f (id double precision, data int);
INSERT INTO f
SELECT i::double precision, (random() * 1000)::int
  FROM generate_series(1, 10000000) AS i;
ANALYZE;
```

#### 4.2.2.1 并行嵌套循环连接

普通并行嵌套循环中，内表并不由worker共享处理；每个worker都要独立处理整个内表。物化嵌套循环会让每个worker各自物化一份内表，worker越多，重复工作越明显。

```sql
testdb=# EXPLAIN SELECT * FROM d, f
                  WHERE d.data = f.data AND f.id < 10000;
                                  QUERY PLAN
-------------------------------------------------------------------------------
 Gather
   Workers Planned: 2
   ->  Nested Loop
         Join Filter: (d.data = f.data)
         ->  Parallel Seq Scan on f
               Filter: (id < '10000'::double precision)
         ->  Materialize
               ->  Seq Scan on d
```

{{< fig num="4.12" src="/img/fig-4-12.png" caption="并行查询中的物化嵌套循环" alt="图4.12 每个worker分别物化并扫描完整内表" />}}

索引嵌套循环更高效：内表扫描仍不共享，但每个worker可用索引只取得匹配行。

```sql
testdb=# EXPLAIN SELECT * FROM d, f
                  WHERE d.id = f.id AND f.id < 10000;
                                  QUERY PLAN
-------------------------------------------------------------------------------
 Gather
   Workers Planned: 2
   ->  Nested Loop
         ->  Parallel Seq Scan on f
               Filter: (id < '10000'::double precision)
         ->  Index Scan using d_id_idx on d
               Index Cond: (id = f.id)
```

{{< fig num="4.13" src="/img/fig-4-13.png" caption="并行查询中的索引嵌套循环" alt="图4.13 worker并行扫描外表并用内表索引定位匹配行" />}}

#### 4.2.2.2 并行归并连接

与嵌套循环类似，普通归并连接要求每个worker独立处理并排序完整内表。若内表能通过有序索引扫描访问，则可避免重复排序，提高效率。

```text
Gather
  -> Merge Join
       Merge Cond: (f.id = d.id)
       -> Sort
            -> Parallel Seq Scan on f
       -> Index Scan using d_id_idx on d
```

#### 4.2.2.3 并行哈希连接

PostgreSQL 9.6和10中，每个worker都为内表构建自己的私有哈希表，造成重复工作与内存开销。PostgreSQL 11引入由`enable_parallel_hash`控制的**Parallel Hash Join**：所有worker协作在DSM中构建一张共享哈希表。

```text
Gather
  -> Parallel Hash Join
       Hash Cond: (f.id = d.id)
       -> Parallel Seq Scan on f
       -> Parallel Hash
            -> Parallel Seq Scan on d
```

### 4.2.3 并行聚合

大多数聚合函数可以并行执行，具体函数是否支持取决于官方聚合函数表中的`Partial Mode`是否为`YES`。计划器根据预计目标行数选择两种主要策略。

#### 4.2.3.1 少量行：leader直接聚合

预计行数很少时，worker负责扫描，`Gather`把原始行返回leader，再由普通`Aggregate`计算结果：

```text
Aggregate
  -> Gather
       Workers Planned: 2
       -> Parallel Seq Scan on d
```

这样避免在每个worker上创建不必要的局部聚合状态。

#### 4.2.3.2 大量行：Partial/Finalize聚合

预计行数很大时，先在worker端减少数据量更高效：

1. 每个worker对本地扫描结果执行`Partial Aggregate`。
2. `Gather`只收集中间聚合状态，而不是全部原始行。
3. `Finalize Aggregate`合并中间结果。

```text
Finalize Aggregate
  -> Gather
       Workers Planned: 2
       -> Partial Aggregate
            -> Parallel Seq Scan on d
```

#### 4.2.3.3 并行聚合的数学基础

合并两个worker的局部结果时，设行数为$n_1$、$n_2$，和为$S_{n_1}$、$S_{n_2}$，平方离差和为$V_{n_1}$、$V_{n_2}$：

$$
\begin{aligned}
S_n &= S_{n_1}+S_{n_2},\\
A_n &= \frac{S_{n_1}+S_{n_2}}{n_1+n_2},\\
V_n &= V_{n_1}+V_{n_2}
 +\frac{n_1n_2}{n_1+n_2}
 \left(\frac{S_{n_1}}{n_1}-\frac{S_{n_2}}{n_2}\right)^2.
\end{aligned}
$$

`Finalize Aggregate`用这些公式合并局部状态。三个或更多worker时重复两两合并，直至得到最终结果；推导见[附录A.1.2](/appendix/#a12-单遍并行方差公式的推导)。
