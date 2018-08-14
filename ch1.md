# 第一章 数据库集簇，数据库，数据表

[TOC]

为了帮助理解后续章节，本章和第二章总结了PostgreSQL的入门知识。本章中，描述了以下几个主题：

* 数据库集簇的逻辑结构
* 数据库集簇的物理结构
* 堆表文件的内部布局
* 表上的数据写入与读取的方法

如果你已经熟悉这些内容，可以跳过本章。

## 1.1 数据库集簇的逻辑结构

**数据库集簇（database cluster）**是PostgreSQL服务管理的数据库集合。 如果你是第一次听到这个定义，对此你会感到疑惑，但PostgreSQL中的术语“数据库集簇”并**不**意味着“一组数据库服务器”。 一个PostgreSQL服务在单台主机上运行，并管理单个数据库集簇。

​	图1.1显示了数据库集簇的逻辑结构。 数据库是数据库对象的集合。 在关系型数据库理论中，数据库对象是用于存储或引用数据的数据结构。 （堆）表是一个典型的例子，还有更多像索引，序列，视图，函数等。 在PostgreSQL中，数据库本身也是数据库对象，并且在逻辑上彼此分离。 所有其他数据库对象（例如，表，索引等）属于它们相对应的数据库。

**图1.1 数据库集簇的逻辑结构**

![](img/fig-1-01.png)

​	在PostgreSQL内部，通过所有数据库对象相应的**对象标识符（Object Identifiers, OID）**进行管理，这些标识符是无符号的4字节整型。取决于数据库对象的类型，数据库对象与相应OID之间的关系存储在相应的[**系统目录**](https://www.postgresql.org/docs/current/static/catalogs.html)中。 例如，`pg_database`和`pg_class`中分别存储了数据库和堆表对象的OID，因此执行以下查询，你可以查找想要的OID：

```sql
sampledb=# SELECT datname, oid FROM pg_database WHERE datname = 'sampledb';
 datname  |  oid  
----------+-------
 sampledb | 16384
(1 row)

sampledb=# SELECT relname, oid FROM pg_class WHERE relname = 'sampletbl';
  relname  |  oid  
-----------+-------
 sampletbl | 18740 
(1 row)
```



## 1.2 数据库集簇的物理结构

​	基本上，一个数据库集簇对应一个称为**基本目录（base directory）**的目录，它包含一些子目录和许多文件。 执行`initdb`命令会在指定目录下创建基本目录，从而初始化一个新的数据库集簇。 虽然不是必须的，但通常将`PGDATA`设置为基本目录。

​	图1.2 展示了一个PostgreSQL中数据库集簇的例子。 一个数据库对应*base*子目录下的一个子目录，其中的每个表和索引在其所属数据库的子目录下都会存储（至少）一个文件。另外， 还有几个包含特定数据和配置文件的子目录。 虽然PostgreSQL支持表空间，但该术语的含义与其他RDBMS不同。 PostgreSQL中的一个表空间对应一个目录，该目录包含基本目录外的一些数据。

**图1.2 数据库集簇示例**

![](img/fig-1-02.png)

在后面的小节中，将阐述数据库集簇的布局，数据库布局以及表与索引相应的文件的布局，并且会描述PostgreSQL中的表空间。

### 1.2.1 数据库集簇的布局

在[官方文档](https://www.postgresql.org/docs/current/static/storage-file-layout.html)中，描述了数据库集簇的布局。 表1.1中列出了文档一部分中的主要文件和子目录：

**表 1.1： 基本目录下的数据库文件和子目录的布局（参考官方文档）**

| 文件                              | 描述                                                         |
| --------------------------------- | ------------------------------------------------------------ |
| PG_VERSION                        | 包含PostgreSQL主版本号的文件                                 |
| pg_hba.conf                       | 控制PosgreSQL客户端身份验证的文件                            |
| pg_ident.conf                     | 用于控制PostgreSQL用户名映射的文件                           |
| postgresql.conf                   | 用于设置配置参数的文件                                       |
| postgresql.auto.conf              | 用于存储在ALTER SYSTEM（版本9.4或更高版本）中设置的配置参数的文件 |
| postmaster.opts                   | 记录服务器上次启动的命令行选项的文件                         |


| 子目录                              | 描述                                                         |
| --------------------------------- | ------------------------------------------------------------ |
| base/                             | 包含每个数据库子目录                                         |
| global/                           | 包含集簇范围的表，例如pg_database，以及pg_control文件的子目录。   |
| pg_commit_ts/                     | 包含事务提交时间戳数据的子目录（9.5或更高版本）。          |
| pg_clog/ (9.6以前) | 包含事务提交状态数据的子目录。它在版本10中重命名为pg_xact。<br />CLOG将在5.4节中描述 |
| pg_dynshmem/                      | 包含动态共享内存子系统中使用的文件的子目录。版本9.4或更高版本。 |
| pg_logical/                       | 包含逻辑解码的状态数据的子目录。版本9.4或更高版本。              |
| pg_multixact/                     | 包含多事务状态数据的子目录                                       |
| pg_notify/                        | 包含`LISTEN`/`NOTIFY`状态数据的子目录                     |
| pg_repslot/                       | 包含复制槽数据的子目录。版本9.4或更高版本。                  |
| pg_serial/                        | 包含已提交的可序列化事务的相关信息的子目录（版本9.1或更高版本） |
| pg_snapshots/                     | 包含导出快照的子目录（版本9.2或更高版本）。 PostgreSQL的函数`pg_export_snapshot`在此子目录中创建快照信息文件。 |
| pg_stat/                          | 包含统计子系统的永久文件的子目录                          |
| pg_stat_tmp/                      | 包含统计子系统的临时文件的子目录                              |
| pg_subtrans/                      | 包含子事务状态数据的子目录                                   |
| pg_tblspc/                        | 包含指向表空间的符号链接的子目录                             |
| pg_twophase/                      | 包含准备好的事务的状态文件的子目录                             |
| pg_wal/ (10以后) | （版本10或更高版本）包含WAL（ Write Ahead Logging）段文件的子目录。<br />它在版本10中从pg_xlog重命名而来。 |
| pg_xact/ (10以后) | （版本10或更高版本）包含事务提交状态数据的子目录。它在版本10中从pg_clog重命名。CLOG将在5.4节中描述。 |
| pg_xlog/ (9.6前) | （版本9.6或更早版本）包含WAL（Write Ahead Logging）段文件的子目录。<br />它在版本10中重命名为pg_wal。 |

### 1.2.2 数据库布局

一个数据库是*base*子目录下的一个子目录；并且数据库目录的名称与数据库的OID相同。 例如，当数据库*sampledb*的OID为16384时，其子目录名称为16384。

```bash
$ cd $PGDATA
$ ls -ld base/16384
drwx------  213 postgres postgres  7242  8 26 16:33 16384
```

### 1.2.3 表与索引相关文件的布局

每个小于1GB的表或索引都在相应的数据库目录中存储为单个文件。表和索引作为数据库对象，在数据库内部是通过OID来管理的，而这些数据文件由变量 *relfilenode* 管理。 表和索引的 *relfilenode* 值通常（但不总是）与相应的OID匹配，细节如下所述。

让我们看一下表`sampletbl`的OID和 *relfilenode*：

```sql
sampledb=# SELECT relname, oid, relfilenode FROM pg_class WHERE relname = 'sampletbl';
  relname  |  oid  | relfilenode
-----------+-------+-------------
 sampletbl | 18740 |       18740 
(1 row)
```

从上面的结果可以看到oid和relfilenode值都相等。还可以看到表`sampletbl`的数据文件路劲是*base/16384/18740*。

```bash
$ cd $PGDATA
$ ls -la base/16384/18740
-rw------- 1 postgres postgres 8192 Apr 21 10:21 base/16384/18740
```

表和索引的 *relfilenode* 值会被一些命令（例如，`TRUNCATE`，`REINDEX`，`CLUSTER`）改变。 例如，如果我们对表`sampletbl`，执行`TRUNCATE`，PostgreSQL会为表分配一个新的 *relfilenode*（18812），删除旧的数据文件（18740），并创建一个新的（18812）。

```sql
sampledb=# TRUNCATE sampletbl;
TRUNCATE TABLE

sampledb=# SELECT relname, oid, relfilenode FROM pg_class WHERE relname = 'sampletbl';
  relname  |  oid  | relfilenode
-----------+-------+-------------
 sampletbl | 18740 |       18812 
(1 row)
```

>  在9.0或更高版本中，内建函数`pg_relation_filepath`能够返回具有指定OID或名称的关系的文件路径名，这非常有用。
>
> ```sql
> sampledb=# SELECT pg_relation_filepath('sampletbl');
>  pg_relation_filepath 
> ----------------------
>  base/16384/18812
> (1 row)
> ```

当表和索引的文件大小超过1GB时，PostgreSQL会创建一个名为 *relfilenode.1* 的新文件并使用。如果新文件已填满，则将创建名为*relfilenode.2*的下一个新文件，依此类推。

```sql
$ cd $PGDATA
$ ls -la -h base/16384/19427*
-rw------- 1 postgres postgres 1.0G  Apr  21 11:16 data/base/16384/19427
-rw------- 1 postgres postgres  45M  Apr  21 11:20 data/base/16384/19427.1
...
```

> 在构建PostgreSQL时，可以使用配置选项`--with-segsize`更改表和索引的最大文件大小。
>

仔细查看数据库子目录，您会发现每个表都有两个相关联的文件，后缀分别为`_fsm`和`_vm`。这些被称为可用空间（free space map）和可见性映射（visibility map），分别存储表文件中每个页面上的可用空间容量和可见性的信息（更多细节见第5.3.4节和第6.2节）。索引只有自己的可用空间映射，没有可见性映射。

具体示例如下所示：

```bash
$ cd $PGDATA
$ ls -la base/16384/18751*
-rw------- 1 postgres postgres  8192 Apr 21 10:21 base/16384/18751
-rw------- 1 postgres postgres 24576 Apr 21 10:18 base/16384/18751_fsm
-rw------- 1 postgres postgres  8192 Apr 21 10:18 base/16384/18751_vm
```

​	在内部，它们也被称为每种关系的**分支（fork）**；可用空间映射是表/索引数据文件的第一个分支（分支编号为1），可见性映射表是数据文件的第二个分支（分支编号为2）。数据文件的分支编号为0。

### 1.2.4 表空间

PostgreSQL中的**表空间（Tablespace）**是基本目录之外的附加数据区域。 此功能在8.0版中实现。

图1.3显示了表空间的内部布局，以及与主数据区域的关系。

**图 1.3 数据库集簇的表空间**

![](img/fig-1-03.png)

​	在执行[`CREATE TABLESPACE`](https://www.postgresql.org/docs/current/static/sql-createtablespace.html)语句时，会在你所指定的目录下被创建表空间。而在该目录下，又会创建特定版本的子目录（例如，`PG_9.4_201409291`）。特定版本的命名方法如下所示。

```
PG_'主版本号'_'目录版本号'
```

​	例如，如果在`/home/postgres/tblspc`中创建一个表空间`new_tblspc`，其oid为16386，则会在表空间下创建一个名如`PG_9.4_201409291`的子目录。

```bash
$ ls -l /home/postgres/tblspc/
total 4
drwx------ 2 postgres postgres 4096 Apr 21 10:08 PG_9.4_201409291
```

​	表空间目录由`pg_tblspc`子目录中的符号链接寻址，链接名称与表空间的OID值相同。

```
$ ls -l $PGDATA/pg_tblspc/
total 0
lrwxrwxrwx 1 postgres postgres 21 Apr 21 10:08 16386 -> /home/postgres/tblspc
```

​	如果在表空间下创建新数据库（OID为16387），则会在特定版本的子目录下创建相应目录。

```bash
$ ls -l /home/postgres/tblspc/PG_9.4_201409291/
total 4
drwx------ 2 postgres postgres 4096 Apr 21 10:10 16387
```

​	如果创建的新表属于基本目录下的数据库，首先，在特定版本的子目录下创建名称与现有数据库OID相同的新目录，然后将新表文件放在创建的目录下。

```sql
sampledb=# CREATE TABLE newtbl (.....) TABLESPACE new_tblspc;

sampledb=# SELECT pg_relation_filepath('newtbl');
             pg_relation_filepath             
----------------------------------------------
 pg_tblspc/16386/PG_9.4_201409291/16384/18894
```



## 1.3 堆表文件的内部布局

​	在数据文件（堆表和索引，以及可用空间映射和可见性映射）内部，它被划分为固定长度的**页（pages）**（或**区块（blocks）**），默认为8192字节（8 KB）。 每个文件中的页从0开始按顺序编号，这些数字称为**区块号（block numbers）**。 如果文件已填满，PostgreSQL通过在文件末尾添加一个新的空页，来增大文件。

​	页的内部布局取决于数据文件类型。 理解后续的章节需要了解这些知识，本节中描述了表的布局。

**图 1.4. 堆表文件的页面布局**

![](img/fig-1-04.png)

表中的页包含如下所述的三种数据：

1. **堆元组（heap tuples）** —— 堆元组就是数据记录本身。它们从页面底部按顺序堆叠。因为需要知道PostgreSQL中的并发控制（CC）和WAL，元组的内部结构将在第5.2节和第9章中阐述。

2. **行指针（line pointer）** ）—— 每个行指针长4个字节，并保存指向每个堆元组的指针。它也被称为**条目指针（item pointer）**。

   行指针组成了一个简单的数组，作为一个元组索引。每个索引项从1开始按顺序编号，并称为**偏移号（offset number）**。当向页中添加新元组时，新的行指针也会被插入数组中，并指向新添加的元组。

3. **首部数据（header data）**  —— 由结构`PageHeaderData`定义的头部数据分配在页的开头处。它长24个字节，包含有关页面的一般信息。该结构的主要变量如下所述。

   + `pd_lsn` —— 此变量存储由此页面的最后一次更改写入的XLOG记录的LSN。它是一个8字节无符号整数，与WAL（Write-Ahead Logging）机制相关。细节在第9章中描述。
   + `pd_checksum` —— 此变量存储此页面的校验和值。（请注意，版本9.3或更高版本支持此变量；在早期版本中，此部分用于存储页面的timelineId。）
   + `pd_lower`，`pd_upper` —— `pd_lower`指向行指针的末尾，`pd_upper`指向最新堆元组的开头。
   + `pd_special` —— 此变量在索引中使用。在堆表的页中，它指向页的末尾。（在索引的页中，它指向特殊空间的开头，它是仅由索引保存的数据区域，根据索引类型包含特定数据，如B-tree，GiST，GiN等）

   ```c
   /* @src/include/storage/bufpage.h */
   
   /*
    * 磁盘页面布局
    *
    * 对任何页面都适用的通用空间管理信息
    *
    *		pd_lsn		- 本页面最近变更对应xlog记录的标识。
    *		pd_checksum - 页面校验和
    *		pd_flags	- 标记位
    *		pd_lower	- 空闲空间开始位置
    *		pd_upper	- 空闲空间结束位置
    *		pd_special	- 特殊空间开始位置
    *		pd_pagesize_version - 页面的大小，以及页面布局的版本号
    *		pd_prune_xid - 本页面中可以修剪的最老的元组中的XID.
    *
    * 缓冲管理器使用LSN来强制WAL的基本规则："WAL需先于数据写入"。直到xlog刷盘位置超过
    * 本页面的LSN之前，不能将缓冲区的脏页刷入磁盘。
    *
    * pd_checksum 存储着页面的校验和，如果本页面配置了校验。0是一个合法的校验和值。 如果页面
    * 没有使用校验和，我们就不会设置这个字段的值；通常这意味着该字段值为0，但如果数据库是从早于
    * 9.3版本从 pg_upgrade升级而来，也可能会出现非零的值。因为那时候这块地方用于存储页面最后
    * 更新时的时间线标识。 注意，并没有标识告诉你页面的标识符到底是有效还是无效的，也没有与之关
    * 联的标记为。这是特意设计成这样的，从而避免了需要依赖页面的具体内容来决定是否校验页面本身。
    *
    * pd_prune_xid是一个提示字段，用于帮助确认剪枝是否有用。目前对于索引页没用。
    *
    * 页面版本编号与页面尺寸被打包成了单个uint16字段，这是有历史原因的：在PostgreSQL7.3之前
    * 并没有页面版本编号这个概念，这样做能让我们假装7.3之前的版本的页面版本编号为0。我们约束页面
    * 的尺寸必需为256的倍数，留下低位的8个位用于页面版本编号。
    *
    * 最小的可行页面大小可能是64字节，能放下页的首部，空闲空间，以及一个最小的元组。当然在实践中
    * 肯定要大得多(默认为8192字节)，所以页面大小必需是256的倍数并不是一个重要限制。而在另一端，
    * 我们最大只能支持32KB的页面，因为 lp_off/lp_len字段都是15bit。
    */
   typedef struct PageHeaderData
   {
   	PageXLogRecPtr 	pd_lsn;			/* 最近应用至本页面XLog记录的LSN */
   	uint16			pd_checksum;	/* 校验和 */
   	uint16		  	pd_flags;		/* 标记位，详情见下 */
   	LocationIndex 	pd_lower;		/* 空闲空间起始位置 */
   	LocationIndex 	pd_upper;		/* 空闲空间终止位置 */
   	LocationIndex 	pd_special;		/* 特殊用途空间的开始位置 */
   	uint16		  	pd_pagesize_version;
   	TransactionId 	pd_prune_xid; 	/* 最老的可修剪XID, 如果没有设置为0 */
   	ItemIdData		pd_linp[FLEXIBLE_ARRAY_MEMBER]; /* 行指针的数组 */
   } PageHeaderData;
   
   
   /* 缓冲区页中的项目指针(item pointer)，也被称为行指针(line pointer)。
    *
    * 在某些情况下，项目指针处于 “使用中”的状态，但在本页中没有任何相关联的存储区域。
    * 按照惯例，lp_len == 0 表示该行指针没有关联存储。独立于其lp_flags的状态. 
    */
   typedef struct ItemIdData
   {
   	unsigned	lp_off:15,		/* 元组偏移量 (相对页面起始处) */
   				lp_flags:2,		/* 行指针的状态，见下 */
   				lp_len:15;		/* 元组的长度，以字节计 */
   } ItemIdData;
   
   /* lp_flags有下列可能的状态，LP_UNUSED的行指针可以立即重用，而其他状态的不行。 */
   #define LP_UNUSED		0		/* unused (lp_len必需始终为0) */
   #define LP_NORMAL		1		/* used (lp_len必需始终>0) */
   #define LP_REDIRECT		2		/* HOT 重定向 (lp_len必需为0) */
   #define LP_DEAD			3		/* 死元组，也许有，也许没有对应的存储 */
   
   ```

   行指针末尾和最新元组开头之间的空白空间称为**可用空间（free space）**或**空洞（hole）**。

   为了识别表中的元组，内部使用元组标识符（TID）。TID包括一对值：包含元组的页的区块编号，以及指向元组的行指针的偏移编号。其典型的用例是索引。更多细节请参见第1.4.2节。

> 结构体`PageHeaderData`定义于[src/include/storage/bufpage.h](https://github.com/postgres/postgres/blob/master/src/include/storage/bufpage.h)中。

此外，超过约2KB（8KB的四分之一）大小的堆元组会使用一种称为 **TOAST（The Oversized-Attribute Storage Technique，超大属性存储技术）** 的方法来存储与管理。详情参阅[PostgreSQL文档](https://www.postgresql.org/docs/current/static/storage-toast.html)。



## 1.4 读写元组的方法

本章的最后，将描述读取及写入堆元组的方法。

### 1.4.1 写入堆元组

假设一个表由一个页面组成，该页面只包含一个堆元组。 此页面的`pd_lower`指向第一个行指针，且该行指针和`pd_upper`都指向第一个堆元组。 见图1.5(a)。

当插入第二个元组时，将其放在第一个元组之后。第二个行指针被插入到第一个行指针的后面，并指向第二个元组。 `pd_lower`更改为指向第二个行指针，`pd_upper`更改为第二个堆元组。 见图1.5(b)。 该页面内的其他首部数据（例如`pd_lsn`，`pg_checksum`，`pg_flag`）也被重写为适当的值; 更多细节在第5.3节和第9章中描述。

**图1.5 堆元组的写入**

![](img/fig-1-05.png)

### 1.4.2 读堆元组

这里概述两种典型的访问方法，顺序扫描和B树索引扫描：

* **顺序扫描** —— 按顺序读取所有页面中的所有元组，通过扫描每一页中的行指针实现。如图1.6(a)。
* **B树索引扫描** —— 索引文件包含索引元组，每个索引元组由索引键，和指向目标堆元组的TID组成。 如果找到了正在查找的键的索引元组，PostgreSQL将使用获取的TID值读取所需的堆元组。 （因为很常见，但本文空间有限，这里没有解释在B树索引中找到索引元组的方法的描述，请参考相关资料）例如，在图1.6(b)中，TID 获得的索引元组的值是`(block = 7，Offset = 2)`。 这意味着目标堆元组是表中第7页的第2个元组，因此PostgreSQL可以避免在页面中进行不必要的扫描，而读取所需的堆元组。

**图 1.6 顺序扫描和索引扫描**

![](img/fig-1-06.png)

> PostgreSQL还支持TID扫描，位图扫描（[Bitmap-Scan](https://wiki.postgresql.org/wiki/Bitmap_Indexes)）和仅索引扫描（Index-Only-Scan）。
>
> TID-Scan是一种通过使用所需元组的TID直接访问元组的方法。 例如，要在表中找到第0个页面中的第一个元组，执行以下查询：
>
> ```sql
> sampledb=# SELECT ctid, data FROM sampletbl WHERE ctid = '(0,1)';
>  ctid  |   data    
> -------+-----------
>  (0,1) | AAAAAAAAA
> (1 row)
> ```
>
> 仅索引扫描将在第7章中详细介绍。

