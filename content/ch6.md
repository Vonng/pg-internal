---
title: 6. 清理过程
weight: 106
breadcrumbs: false
---

**清理（VACUUM）** 是一种维护过程，有助于 PostgreSQL 的持久运行。它的两个主要任务是删除死元组，以及冻结事务标识，两者都在第5.10节中简要提到过。

为了移除死元组，清理过程有两种模式：**并发清理（Concurrent Vacuum）** 与**完整清理（Full Vacuum）** 。并发清理（通常简称为`VACUUM`）会删除表文件每个页面中的死元组，而其他事务可以在其运行时继续读取该表。
相反，完整清理不仅会移除整个文件中所有的死元组，还会对整个文件中所有的活元组进行碎片整理。而其他事务在完整清理运行时无法访问该表。

尽管清理过程对PostgreSQL至关重要，但与其他功能相比，它的改进相对其他功能而言要慢一些。例如在8.0版本之前，清理过程必须手动执行（通过`psql`实用程序或使用`cron`守护进程）。直到2005年实现了`autovacuum`守护进程时，这一过程才实现了自动化。

由于清理过程涉及到全表扫描，因此该过程代价高昂。在版本8.4（2009）中引入了 **可见性映射（Visibility Map, VM）** 来提高移除死元组的效率。在版本 9.6（2016）中增强了VM，从而改善了冻结过程的表现。

6.1节概述了并发清理的过程，而后续部分的内容如下所示：

* 可见性映射
* **冻结（Freeze）** 过程
* 移除不必要的clog文件
* **自动清理（AutoVacuum）** 守护进程
* 完整清理

## 6.1 并发清理概述

清理过程为指定的表，或数据库中的所有表执行以下任务。

1. 移除死元组
   * 移除每一页中的死元组，并对每一页内的活元组进行碎片整理。
   * 移除指向死元组的索引元组。
2. 冻结旧的事务标识（`txid`）
   * 如有必要，冻结旧元组的事务标识（txid）。
   * 更新与冻结事务标识相关的系统视图（`pg_database`与`pg_class`）。
   * 如果可能，移除非必需的提交日志（clog）。
3. 其他
   * 更新已处理表的空闲空间映射（FSM）和可见性映射（VM）。
   * 更新一些统计信息（`pg_stat_all_tables`等）。

这里假设读者已经熟悉以下术语：死元组，冻结事务标识，FSM，clog；如果读者不熟悉这些术语的含义，请参阅[第5章](/ch5)。VM将在第6.2节中介绍。

以下伪代码描述了清理的过程。



> ### 伪码：并发清理
>
> ```pseudocode
> (1)     FOR each table
> (2)         在目标表上获取 ShareUpdateExclusiveLock 锁
>     
>             /* 第一部分 */
> (3)         扫描所有页面，定位死元组；如有必要，冻结过老的元组。
> (4)         如果存在，移除指向死元组的索引元组。
>     
>             /* 第二部分 */
> (5)         FOR each page of the table
> (6)             移除死元组，重排本页内的活元组。
> (7)             更新 FSM 与 VM
>             END FOR
>     
>             /* 第三部分 */
> (8)         如果可能，截断最后的页面。
> (9)         更新系统数据字典与统计信息
>             释放ShareUpdateExclusiveLock锁
>         END FOR
>     
>         /* 后续处理 */
> (10)    更新统计信息与系统数据字典
> (11)    如果可能，移除没有必要的文件，以及clog中的文件。
> ```
>
> 1. 从指定的表集中依次处理每一张表。
> 2. 获取表上的`ShareUpdateExclusiveLock`锁， 此锁允许其他事务对该表进行读取。
> 3. 扫描表中所有的页面，以获取所有的死元组，并在必要时冻结旧元组。
> 4. 删除指向相应死元组的索引元组（如果存在）。
> 5. 对表的每个页面执行步骤(6)和(7)中的操作
> 6. 移除死元组，并重新分配页面中的活元组。
> 7. 更新目标表对应的FSM与VM。
> 8. 如果最后一个页面没有任何元组，则截断最后一页。
> 9. 更新与**目标表**清理过程相关的统计数据和系统视图。
> 10. 更新与清理过程相关的统计数据和系统视图。
> 11. 如果可能，移除clog中非必需的文件与页面。

该伪码分为两大块：一块是依次处理表的循环，一块是后处理逻辑。而循环块又能分为三个部分，每一个部分都有各自的任务。接下来会描述这三个部分，以及后处理的逻辑。

### 6.1.1 第一部分

这一部分执行冻结处理，并删除指向死元组的索引元组。

首先，PostgreSQL扫描目标表以构建死元组列表，如果可能的话，还会冻结旧元组。该列表存储在本地内存中的[`maintenance_work_mem`](https://www.postgresql.org/docs/current/static/runtime-config-resource.html#GUC-MAINTENANCE-WORK-MEM)里（维护用的工作内存）。冻结处理将在第6.3节中介绍。

扫描完成后，PostgreSQL根据构建得到的死元组列表来删除索引元组。该过程在内部被称为“**清除阶段（cleanup stage）**”。不用说，该过程代价高昂。在10或更早版本中始终会执行清除阶段。在11或更高版本中，如果目标索引是B树，是否执行清除阶段由配置参数[`vacuum_cleanup_index_scale_factor`](https://www.postgresql.org/docs/devel/static/runtime-config-resource.html#RUNTIME-CONFIG-INDEX-VACUUM)决定。详细信息请参考[此参数的说明](https://www.postgresql.org/docs/devel/static/runtime-config-resource.html#RUNTIME-CONFIG-INDEX-VACUUM)。

当`maintenance_work_mem`已满，且未完成全部扫描时，PostgreSQL继续进行后续任务，即步骤4到7；完成后再重新返回步骤3并继续扫描。

### 6.1.2 第二部分

这一部分会移除死元组，并逐页更新FSM和VM。图6.1展示了一个例子：

**图6.1 删除死元组**

![](/img/fig-6-01.png)
假设该表包含三个页面，这里先关注0号页面（即第一个页面）。该页面包含三条元组， 其中`Tuple_2`是一条死元组，如图6.1(1)所示。在这里PostgreSQL移除了`Tuple_2`，并重排剩余元组来整理碎片空间，然后更新该页面的FSM和VM，如图6.1(2)所示。 PostgreSQL不断重复该过程直至最后一页。

请注意，非必需的行指针是不会被移除的，它们会在将来被重用。因为如果移除了行指针，就必须同时更新所有相关索引中的索引元组。

### 6.1.3 第三部分

第三部分会针对每个表，更新与清理过程相关的统计信息和系统视图。

此外，如果最后一页中没有元组，则该页会从表文件中被截断。

### 6.1.4 后续处理

当处理完成后，PostgreSQL会更新与清理过程相关的几个统计数据，以及相关的系统视图；如果可能的话，它还会移除部分非必需的clog（第6.4节）。

> 清理过程使用8.5节中将描述的**环形缓冲区（ring buffer）**。因此处理过的页面不会缓存在共享缓冲区中。
>

## 6.2 可见性映射

清理过程的代价高昂，因此PostgreSQL在8.4版中引入了VM，用于减小清理的开销。

VM的基本概念很简单。 每个表都拥有各自的可见性映射，用于保存表文件中每个页面的可见性。 页面的可见性确定了每个页面是否包含死元组。清理过程可以跳过没有死元组的页面。

图6.2展示了VM的使用方式。 假设该表包含三个页面，第0页和第2页包含死元组，而第1页不包含死元组。 表的可见性映射中保存着哪些页面包含死元组的信息。 在这种情况下，清理过程可以参考VM中的信息，跳过第一个页面。

**图6.2 VM的使用方式**

![](/img/fig-6-02.png)

每个VM由一个或多个8 KB页面组成，文件以后缀`_vm`存储。 例如，一个表文件的`relfilenode`是18751，其FSM（`18751_fsm`）和VM（`18751_vm`）文件如下所示。

```bash
$ cd $PGDATA
$ ls -la base/16384/18751*
-rw------- 1 postgres postgres  8192 Apr 21 10:21 base/16384/18751
-rw------- 1 postgres postgres 24576 Apr 21 10:18 base/16384/18751_fsm
-rw------- 1 postgres postgres  8192 Apr 21 10:18 base/16384/18751_vm
```

### 6.2.1 可见性映射的改进

可见性映射在9.6版中进行了加强，以提高冻结处理的效率。新的VM除了显示页面可见性之外，还包含了页面中元组是否全部冻结的信息，参见第6.3.3节。

## 6.3 冻结过程

冻结过程有两种模式，依特定条件而择其一执行。为方便起见，将这两种模式分别称为**惰性模式（lazy mode）**和**迫切模式（eager mode）**。

>
> **并发清理（Concurrent VACUUM）** 通常在内部被称为“**惰性清理（lazy vacuum）**”。但是，本文中定义的惰性模式是 **冻结过程（Freeze Processing）** 执行的模式。
>


冻结过程通常以惰性模式运行；但当满足特定条件时，也会以迫切模式运行。在惰性模式下，冻结处理仅使用目标表对应的VM扫描包含死元组的页面。迫切模式相则反，它会扫描所有的页面，无论其是否包含死元组，它还会更新与冻结处理相关的系统视图，并在可能的情况下删除不必要的clog。

第6.3.1和6.3.2节分别描述了这两种模式；第6.3.3节描述了改进后的迫切模式冻结过程。

### 6.3.1 惰性模式

当开始冻结处理时，PostgreSQL计算`freezeLimit_txid`，并冻结`t_xmin`小于`freezeLimit_txid`的元组。

`freezeLimit_txid`定义如下：
$$
\begin{align}
	\verb|freezeLimit_txid| = (\verb|OldestXmin| - \verb|vacuum_freeze_min_age|)
\end{align}
$$


而`OldestXmin`是当前正在运行的事务中最早的**事务标识（txid）**。 举个例子，如果在执行`VACUUM`命令时，还有其他三个事务正在运行，且其`txid`分别为`100,101,102`，那么这里`OldestXmin`就是100。如果不存在其他事务，`OldestXmin` 就是执行此`VACUUM`命令的事务标识。 这里`vacuum_freeze_min_age`是一个配置参数（默认值为`50,000,000`）。

图6.3给出了一个具体的例子。这里`Table_1`由三个页面组成，每个页面包含三条元组。 执行`VACUUM`命令时，当前`txid`为`50,002,500`且没有其他事务。在这种情况下，`OldestXmin`就是`50,002,500`；因此`freezeLimit_txid`为`2500`。冻结过程按照如下步骤执行。

**图6.3 冻结元组——惰性模式**

![](/img/fig-6-03.png)

* 第0页：

   三条元组被冻结，因为所有元组的`t_xmin`值都小于`freezeLimit_txid`。此外，因为`Tuple_1`是一条死元组，因而在该清理过程中被移除。

* 第1页：

  通过引用可见性映射（从VM中发现该页面所有元组都可见），清理过程跳过了对该页面的清理。

* 第2页：

  `Tuple_7`和`Tuple_8`被冻结，且`Tuple_7`被移除。

在完成清理过程之前，与清理相关的统计数据会被更新，例如`pg_stat_all_tables`视图中的`n_live_tup`，`n_dead_tup`，`last_vacuum`，`vacuum_count`等字段。

如上例所示，因为惰性模式可能会跳过页面，它可能无法冻结所有需要冻结的元组。

### 6.3.2 迫切模式

迫切模式弥补了惰性模式的缺陷。它会扫描所有页面，检查表中的所有元组，更新相关的系统视图，并在可能时删除非必需的clog文件与页面。

当满足以下条件时，会执行迫切模式。
$$
\begin{align}
	\verb|pg_database.datfrozenxid| < (\verb|OldestXmin| - \verb|vacuum_freeze_table_age|)
\end{align}
$$
在上面的条件中，`pg_database.datfrozenxid`是系统视图`pg_database`中的列，并保存着每个数据库中最老的已冻结的事务标识。细节将在后面描述；因此这里我们假设所有`pg_database.datfrozenxid`的值都是`1821`（这是在9.5版本中安装新数据库集群之后的初始值）。 `vacuum_freeze_table_age`是配置参数（默认为`150,000,000`）。

图6.4给出了一个具体的例子。在表1中，`Tuple_1`和`Tuple_7`都已经被删除。`Tuple_10`和`Tuple_11`则已经插入第2页中。执行`VACUUM`命令时的事务标识为`150,002,000`，且没有其他事务。因此，`OldestXmin=150,002,000`，`freezeLimit_txid=100,002,000`。在这种情况下满足了上述条件：因为`1821 < (150002000 - 150000000)`	，因而冻结过程会以迫切模式执行，如下所示。

（注意，这里是版本9.5或更早版本的行为；最新版本的行为将在第6.3.3节中描述。）

**图6.4 冻结旧元组——迫切模式（9.5或更早版本）**

![](/img/fig-6-04.png)

* 第0页：

  即使所有元组都被冻结，也会检查`Tuple_2`和`Tuple_3`。

* 第1页：

  此页面中的三条元组都会被冻结，因为所有元组的`t_xmin`值都小于`freezeLimit_txid`。注意在惰性模式下会跳过此页面。

* 第2页：

  将`Tuple_10`冻结，而`Tuple_11`没有冻结。

冻结一张表后，目标表的`pg_class.relfrozenxid`将被更新。 [`pg_class`](https://www.postgresql.org/docs/current/catalog-pg-class.html)是一个系统视图，每个`pg_class.relfrozenxid`列都保存着相应表的最近冻结的事务标识。本例中表1的`pg_class.relfrozenxid`会被更新为当前的`freezeLimit_txid`（即`100,002,000`），这意味着表1中`t_xmin`小于`100,002,000`的所有元组都已被冻结。

在完成清理过程之前，必要时会更新`pg_database.datfrozenxid`。每个`pg_database.datfrozenxid`列都包含相应数据库中的最小`pg_class.relfrozenxid`。例如，如果在迫切模式下仅仅对表1做冻结处理，则不会更新该数据库的`pg_database.datfrozenxid`，因为其他关系的`pg_class.relfrozenxid`（当前数据库可见的其他表和系统视图）还没有发生变化，如图6.5(1)所示。如果当前数据库中的所有关系都以迫切模式冻结，则数据库的`pg_database.datfrozenxid`就会被更新，因为此数据库的所有关系的`pg_class.relfrozenxid`都被更新为当前的`freezeLimit txid`，如图6.5(2)所示。

**图6.5  `pg_database.datfrozenxid`与`pg_class.relfrozenxid`之间的关系**

![](/img/fig-6-05.png)

> ####  如何显示`pg_class.relfrozenxid`与`pg_database.datfrozenxid`
>
> 如下所示，第一个查询显示`testdb`数据库中所有可见关系的`relfrozenxid`，第二个查询显示`testdb`数据库的`pg_database.datfrozenxld`。
>
> ```sql
> testdb=# VACUUM table_1;
> VACUUM
> 
> testdb=# SELECT n.nspname as "Schema", c.relname as "Name", c.relfrozenxid
>              FROM pg_catalog.pg_class c
>              LEFT JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
>              WHERE c.relkind IN ('r','')
>                    AND n.nspname <> 'information_schema' 
>                    AND n.nspname !~ '^pg_toast'
>                    AND pg_catalog.pg_table_is_visible(c.oid)
>              ORDER BY c.relfrozenxid::text::bigint DESC;
> 
>    Schema   |            Name         | relfrozenxid 
> ------------+-------------------------+--------------
>  public     | table_1                 |    100002000
>  public     | table_2                 |         1846
>  pg_catalog | pg_database             |         1827
>  pg_catalog | pg_user_mapping         |         1821
>  pg_catalog | pg_largeobject          |         1821
> 
> ...
> 
>  pg_catalog | pg_transform            |         1821
> (57 rows)
> 
> testdb=# SELECT datname, datfrozenxid FROM pg_database 
>             WHERE datname = 'testdb';
>  datname | datfrozenxid 
> ---------+--------------
>  testdb  |         1821
> (1 row)
> ```

> #### `FREEZE`选项
>
> 带有`FREEZE`选项的`VACUUM`命令会强制冻结指定表中的所有事务标识。虽然这是在迫切模式下执行的，但这里`freezeLimit`会被设置为`OldestXmin`（而不是`OldestXmin - vacuum_freeze_min_age`）。 例如当`txid=5000`的事务执行`VACUUM FULL`命令，且没有其他正在运行的事务时，`OldesXmin`会被设置为5000，而`t_xmin`小于5000的元组将会被冻结。

### 6.3.3 改进迫切模式中的冻结过程

9.5或更早版本中的迫切模式效率不高，因为它始终会扫描所有页面。 比如在第6.3.2节的例子中，尽管第0页中所有元组都被冻结，但也会被扫描。

为了解决这一问题，9.6版本改进了可见性映射VM与冻结过程。如第6.2.1节所述，新VM包含着每个页面中所有元组是否都已被冻结的信息。在迫切模式下进行冻结处理时，可以跳过仅包含冻结元组的页面。

图6.6给出了一个例子。 根据VM中的信息，冻结此表时会跳过第0页。在更新完1号页面后，相关的VM信息会被更新，因为该页中所有的元组都已经被冻结了。

**图6.6  冻结旧元组——迫切模式（9.6或更高版本）**

![](/img/fig-6-06.png)

## 6.4 移除不必要的提交日志文件

如5.4节中所述，**提交日志（clog）** 存储着事务的状态。 当更新`pg_database.datfrozenxid`时，PostgreSQL会尝试删除不必要的clog文件。 注意相应的clog页面也会被删除。

图6.7给出了一个例子。 如果clog文件`0002`中包含最小的`pg_database.datfrozenxid`，则可以删除旧文件（`0000`和`0001`），因为存储在这些文件中的所有事务在整个数据库集簇中已经被视为冻结了。

**图6.7  删除不必要的clog文件和页面**

![](/img/fig-6-07.png)

> ###  `pg_database.datfrozenxid`与clog文件
>
> 下面展示了`pg_database.datfrozenxid`与clog文件的实际输出
>
> ```bash
> $ psql testdb -c "SELECT datname, datfrozenxid FROM pg_database"
>   datname  | datfrozenxid 
> -----------+--------------
>  template1 |      7308883
>  template0 |      7556347
>  postgres  |      7339732
>  testdb    |      7506298
> (4 rows)
> 
> $ ls -la -h data/pg_clog/	# 10或更新的版本, "ls -la -h data/pg_xact/"
> total 316K
> drwx------  2 postgres postgres   28 Dec 29 17:15 .
> drwx------ 20 postgres postgres 4.0K Dec 29 17:13 ..
> -rw-------  1 postgres postgres 256K Dec 29 17:15 0006
> -rw-------  1 postgres postgres  56K Dec 29 17:15 0007
> ```

## 6.5 自动清理守护进程

**自动清理（AutoVacuum）**守护进程已经将清理过程自动化，因此PostgreSQL运维起来非常简单。

自动清理守护程序周期性地唤起几个`autovacuum_worker`进程，默认情况下会每分钟唤醒一次（由参数[`autovacuum_naptime`](https://www.postgresql.org/docs/current/runtime-config-autovacuum.html#GUC-AUTOVACUUM-NAPTIME)定义），每次唤起三个工作进程（由[`autovacuum_max_works`](https://www.postgresql.org/docs/current/runtime-config-autovacuum.html#GUC-AUTOVACUUM-MAX-WORKERS)定义）。

自动清理守护进程唤起的`autovacuum`工作进程会依次对各个表执行并发清理，从而将对数据库活动的影响降至最低。

> ###### 关于如何维护`AUTOVACUUM`
>
> 参考文章：[PostgreSQL中的Autovacuum调参，Autovacuum内幕][https://www.percona.com/blog/2018/08/10/tuning-autovacuum-in-postgresql-and-autovacuum-internals/]

## 6.6 完整清理（`FULL VACUUM`）

虽然并发清理对于运维至关重要，但光有它还不够。比如，即使删除了许多死元组，也无法压缩表大小的情况。

图6.8给出了一个极端的例子。假设一个表由三个页面组成，每个页面包含六条元组。执行以下`DELETE`命令以删除元组，并执行`VACUUM`命令以移除死元组：

**图6.8 并发清理的缺陷示例**

![](/img/fig-6-08.png)

```sql
testdb=# DELETE FROM tbl WHERE id % 6 != 0;
testdb=# VACUUM tbl;
```

死元组虽然都被移除了，但表的尺寸没有减小。 这种情况既浪费了磁盘空间，又会对数据库性能产生负面影响。 例如在上面的例子中，当读取表中的三条元组时，必须从磁盘加载三个页面。

为了解决这种情况，PostgreSQL提供了**完整清理**模式。 图6.9概述了该模式。

**图6.9 完整清理模式概述**

![](/img/fig-6-09.png)

1. 创建新的表文件：见图6.9(1)

   当对表执行`VACUUM FULL`命令时，PostgreSQL首先获取表上的`AccessExclusiveLock`锁，并创建一个大小为8 KB的新的表文件。 `AccessExclusiveLock`锁不允许任何其他访问。

2. 将活元组复制到新表：见图6.9(2)

   PostgreSQL只将旧表文件中的活元组复制到新表中。

3. 删除旧文件，重建索引，并更新统计信息，FSM和VM，见图6.9(3)

   复制完所有活元组后，PostgreSQL将删除旧文件，重建所有相关的表索引，更新表的FSM和VM，并更新相关的统计信息和系统视图。

完整清理的伪代码如下所示：

> #### 伪代码：完整清理
>
> ```pseudocode
> (1)     FOR each table
> (2)         获取表上的AccessExclusiveLock锁
> (3)         创建一个新的表文件
> (4)         FOR 每个活元组 in 老表
> (5)             将活元组拷贝到新表中
> (6)             如果有必要，冻结该元组。
>             END FOR
> (7)         移除旧的表文件
> (8)         重建所有索引
> (9)         更新FSM与VM
> (10)        更新统计信息
>             释放AccessExclusiveLock锁
>         END FOR
> (11)    移除不必要的clog文件
> ```

使用`VACUUM FULL`命令时，应当考虑两点。

1. 当执行完整清理时，没有人可以访问（读/写）表。
2. 最多会临时使用两倍于表的磁盘空间；因此在处理大表时，有必要检查剩余磁盘容量。

> ### 什么时候该使用`VACUUM FULL`？
>
> 不幸的是，并没有关于什么时候该执行`VACUUM FULL`的最佳实践。但是扩展[`pg_freespacemap`](https://www.postgresql.org/docs/current/static/pgfreespacemap.html)可能会给出很好的建议。
>
> 以下查询给出了表的平均空间空闲率。
>
> ```sql
> testdb=# CREATE EXTENSION pg_freespacemap;
> CREATE EXTENSION
> 
> testdb=# SELECT count(*) as "number of pages",
>        pg_size_pretty(cast(avg(avail) as bigint)) as "Av. freespace size",
>        round(100 * avg(avail)/8192 ,2) as "Av. freespace ratio"
>        FROM pg_freespace('accounts');
>  number of pages | Av. freespace size | Av. freespace ratio 
> -----------------+--------------------+---------------------
>             1640 | 99 bytes           |                1.21
> (1 row)
> ```
>
> 从上面的结果可以看出，没有多少空闲空间。
>
> 如果删除几乎所有的元组，并执行`VACUUM`命令，则可以发现每个页面几乎都是空的。
>
> ```sql
> testdb=# DELETE FROM accounts WHERE aid %10 != 0 OR aid < 100;
> DELETE 90009
> 
> testdb=# VACUUM accounts;
> VACUUM
> 
> testdb=# SELECT count(*) as "number of pages",
>        pg_size_pretty(cast(avg(avail) as bigint)) as "Av. freespace size",
>        round(100 * avg(avail)/8192 ,2) as "Av. freespace ratio"
>        FROM pg_freespace('accounts');
>  number of pages | Av. freespace size | Av. freespace ratio 
> -----------------+--------------------+---------------------
>             1640 | 7124 bytes         |               86.97
> (1 row)
> ```
>
> 以下查询检查特定表中每个页面的自由空间占比。
>
> ```sql
> testdb=# SELECT *, round(100 * avail/8192 ,2) as "freespace ratio"
>                 FROM pg_freespace('accounts');
>  blkno | avail | freespace ratio 
> -------+-------+-----------------
>      0 |  7904 |           96.00
>      1 |  7520 |           91.00
>      2 |  7136 |           87.00
>      3 |  7136 |           87.00
>      4 |  7136 |           87.00
>      5 |  7136 |           87.00
> ....
> ```
>
> 执行`VACUUM FULL`后会发现表被压实了。
>
> ```sql
> testdb=# VACUUM FULL accounts;
> VACUUM
> testdb=# SELECT count(*) as "number of blocks",
>        pg_size_pretty(cast(avg(avail) as bigint)) as "Av. freespace size",
>        round(100 * avg(avail)/8192 ,2) as "Av. freespace ratio"
>        FROM pg_freespace('accounts');
>  number of pages | Av. freespace size | Av. freespace ratio 
> -----------------+--------------------+---------------------
>              164 | 0 bytes            |                0.00
> (1 row)
> ```

