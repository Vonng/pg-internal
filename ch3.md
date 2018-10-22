# 第三章 查询处理

[TOC]

查询处理是PostgreSQL中最为复杂的子系统。如PostgreSQL[官方文档](https://www.postgresql.org/docs/current/static/features.html)所述，PostgreSQL支持SQL2011标准中的大多数特性，查询处理子系统能够高效地处理这些SQL。本章概述了查询处理的流程，特别关注了查询优化的部分。

本章包括下面三个部分：

+ 第一部分：3.1节

   本节概述了PostgreSQL的查询处理流程。

+ 第二部分：3.2~3.4节

   这一部分描述了获取单表查询上最优执行计划的步骤。3.2节讨论了代价估计的过程，3.3节说明了创建计划树的过程，3.4节简要描述了执行器的工作过程。

+ 第三部分：3.5~3.6节

  这一部分描述了获取多表查询上最优执行计划的步骤。3.5节介绍了三种连接算法：**嵌套循环连接（Nested Loop Join）**，**归并连接（Merge Join）** ，**散列连接（Hash Join）**。3.6节说明了为多表查询创建计划树的过程。

PostgreSQL支持三种技术上很有趣，而且也很实用的功能：[**外部数据包装（Foreign Data Wrapper, FDW）**](https://www.postgresql.org/docs/current/static/fdwhandler.html)，[**并行查询**](https://www.postgresql.org/docs/current/static/parallel-query.html)，以及版本11即将支持的[JIT编译](https://www.postgresql.org/docs/11/static/jit-reason.html)。前两者将在第4章中描述，JIT编译超出范围本书的范围，详见[官方文档](https://www.postgresql.org/docs/11/static/jit-reason.html)。



## 3.1 概览

在PostgreSQL中，尽管在9.6版本后有了基于多个后台工作进程的并行查询，但基本上还是每个连接对应一个后端进程，后端进程由五个子系统组成，如下所示：

1. **解析器（Parser）**

   解析器根据SQL语句生成一颗语法解析树（parse tree） 。

2. **分析器（Analyzer）**

   分析器对语法解析树进行语义分析，生成一颗查询树（query tree）。

3. **重写器（Rewriter）**

   重写器按照[规则系统](https://www.postgresql.org/docs/current/static/rules.html)中存在的规则，对查询树进行改写。

4. **计划器（Planner）**

   计划器基于查询树，生成一颗执行最为高效的计划树（plan tree）；

5. **执行器（Executor）**

   执行器按照计划树中的顺序访问表和索引，执行相应查询；

**图3.1 查询处理**

![QueryProcessing](img/fig-3-01.png)



本节将概述这些子系统。由于计划器和执行器很复杂，后面的章节会对这些函数的细节进行阐述。

> PostgreSQL的查询处理在[官方文档](http://www.postgresql.org/docs/current/static/overview.html)中有详细的描述

### 3.1.1 解析器（Parser）

解析器基于SQL语句的文本，生成一颗后续子系统可以理解的语法解析树。下面给出了一个例子。

考虑以下查询。

```sql
testdb=# SELECT id, data FROM tbl_a WHERE id < 300 ORDER BY data;
```

语法解析树的根节点是一个定义在`parsenodes.h`中 `SelectStmt`数据结构。图3.2(a)展示了一个查询，而图3.2(b)则是该查询对应的语法解析树。

```c
typedef struct SelectStmt
{
        NodeTag         type;

        /* 这些字段只会在SelectStmts“叶节点”中使用 */
        List       *distinctClause;     /* NULL, DISTINCT ON表达式列表, or
                                         * lcons(NIL,NIL) for all (SELECT DISTINCT) */
        IntoClause *intoClause;         /* SELECT INTO 的目标 */
        List       *targetList;         /* 结果目标列表 (ResTarget) */
        List       *fromClause;         /* FROM 子句 */
        Node       *whereClause;        /* WHERE 限定条件 */
        List       *groupClause;        /* GROUP BY 子句 */
        Node       *havingClause;       /* HAVING 条件表达式 */
        List       *windowClause;       /* WINDOW window_name AS (...), ... */

        /*  在一个表示值列表的叶节点中，上面的字段全都为空，而这个字段会被设置。
         * 注意这个子列表中的元素仅仅是表达式，没有ResTarget的修饰，还需要注意列表元素可能为
         * DEFAULT (表示一个 SetToDefault 节点)，而无论值列表的上下文。 
         * 由分析阶段决定否合法并拒绝。      */
        List       *valuesLists;        /* 未转换的表达式列表 */

        /* 这些字段会同时在SelectStmts叶节点与SelectStmts上层节点中使用 */
        List       *sortClause;         /* 排序子句 (排序依据的列表) */
        Node       *limitOffset;        /* 需要跳过的元组数目 */
        Node       *limitCount;         /* 需要返回的元组数目 */
        List       *lockingClause;      /* FOR UPDATE (锁子句的列表) */
        WithClause *withClause;         /* WITH 子句 */

        /*
         * 这些字段只会在上层的 SelectStmts 中出现
         */
        SetOperation op;                /* set 操作的类型 */
        bool            all;            /* 是否指明了 ALL 选项? */
        struct SelectStmt *larg;        /* 左子节点 */
        struct SelectStmt *rarg;        /* 右子节点 */
        /* Eventually add fields for CORRESPONDING spec here */
} SelectStmt;
```

**图. 3.2. 语法解析树的例子**

![ParseTree](img/fig-3-02.png)

`SELECT`查询中的元素和语法解析树中的元素有着对应关系。比如，(1)是目标列表中的一个元素，与目标表的*'id'*列相对应，(4)是一个`WHERE`子句，诸如此类。

当解析器生成语法分析树时只会检查语法，只有当查询中出现语法错误时才会返回错误。解析器并不会检查输入查询的语义，举个例子，如果查询中包含一个不存在的表名，解析器并不会报错，语义检查由分析器负责。



### 3.1.2 分析器（Analyzer）

分析器对解析器产出的语法解析树进行语义分析，生成一颗查询树。

查询树的根是[`parsenode.h`](https://github.com/postgres/postgres/blob/master/src/include/nodes/parsenodes.h)中定义的一个`Query`数据结构，这个结构包含了对应查询的元数据，比如命令的类型（`SELECT/INSERT`等），还包括了一些叶子节点，叶子节点由列表或树组成，包含了特定子句相应的数据。

```c
/*
 * Query -
 *	  解析与分析过程会将所有的语句转换为一颗查询树，供重写器与计划器用于进一步的处理。
 *    功能语句（即不可优化的语句）会设置utilityStmt字段，而Query结构本身基本上是空的。
 *	  DECLARE CURSOR 是一个特例：它的形式与SELECT类似，但原始的DeclareCursorStmt会
 *    被放在 utilityStmt 字段中。
 *    计划过程会将查询树转换为一颗计划树，计划树的根节点是一个PlannedStmt结构
 *    执行器不会用到查询树结构
 */
typedef struct Query
{
	NodeTag		type;
	CmdType		commandType;		/* select|insert|update|delete|utility */
	QuerySource querySource;		/* 我来自哪里? */
	uint32		queryId;		    /* 查询标识符 (可由插件配置) */

	bool		canSetTag;		    /* 我设置了命令结果标签吗? */
	Node	   	*utilityStmt;		/* 如果这是一条DECLARE CURSOR或不可优化的语句 */
	int		resultRelation; 	    /* 对增删改语句而言是目标关系的索引; SELECT为0 */
	bool		hasAggs;		    /* 是否在目标列表或having表达式中指定了聚合函数 */
	bool		hasWindowFuncs; 	/* tlist是否包含窗口函数 */
	bool		hasSubLinks;		/* 是否包含子查询SubLink */
	bool		hasDistinctOn;		/* 是否包含来自DISTINCT ON的distinct子句 */
	bool		hasRecursive;		/* 是否制定了WITH RECURSIVE */
	bool		hasModifyingCTE;	/* 是否在WITH子句中包含了INSERT/UPDATE/DELETE */
	bool		hasForUpdate;		/* 是否指定了FOR [KEY] UPDATE/SHARE*/
	bool		hasRowSecurity; 	/* 是否应用了行安全策略 */
	List	   	*cteList;		    /* CTE列表 */
	List	   	*rtable;		    /* 范围表项目列表 */
	FromExpr   	*jointree;		    /* 表连接树 (FROM 与 WHERE 子句) */
	List	   	*targetList;		/* 目标列表 (TargetEntry的列表) */
	List	   	*withCheckOptions;	/* WithCheckOption的列表 */
	OnConflictExpr 	*onConflict; 	/* ON CONFLICT DO [NOTHING | UPDATE] */
	List	   	*returningList;		/* 返回值列表(TargetEntry的列表) */
	List	   	*groupClause;		/* SortGroupClause的列表 */
	List	   	*groupingSets;		/* 如果有，GroupingSet的列表 */
	Node	   	*havingQual;		/* 分组的Having条件列表 */
	List	   	*windowClause;		/* 窗口子句列表 */
	List	   	*distinctClause; 	/* SortGroupClause列表 */
	List	   	*sortClause;		/* SortGroupClause列表 */
	Node	   	*limitOffset;		/* Offset跳过元组数目 (int8 表达式) */
	Node	   	*limitCount;		/* Limit返回元组数目 (int8 表达式) */
	List	   	*rowMarks;          /* RowMarkClause列表 */
	Node	   	*setOperations;		/* 如果是UNION/INTERSECT/EXCEPT的顶层查询，则为集合操作 列表 */
	List	   	*constraintDeps; 	/* 确认查询语义是否合法时，所依赖约束对象的OID列表 */
} Query;
```

**图3.3 查询树一例**

![QueyTree](img/fig-3-03.png)

简要介绍一下上图中的查询树：

+ *targetlist* 是查询结果中**列（Column）**的列表。在本例中该列表包含两列：*id* 和*data*。如果在输入的查询树中使用了`*`（星号），那么分析器会将其显式替换为所有具体的列。
+ 范围表*rtable*是该查询所用到关系的列表。本例中该变量包含了表*tbl_a*的信息，如该表的表名与*oid*。
+ 连接树*jointree*存储着`FROM`和`WHERE`子句的相关信息。
+ 排序子句*sortClause*是`SortGroupClause`结构体的列表。

[官方文档](http://www.postgresql.org/docs/current/static/querytree.html)描述了查询树的细节。

### 3.1.3 重写器（Rewriter）

PostgreSQL的[规则系统](https://www.postgresql.org/docs/current/static/rules.html)正是依赖重写器实现的，当需要时，重写器会根据存储在`pg_rules`中的规则对查询树进行转换。规则系统本身也是一个很有趣的系统，但本章略去了关于规则系统和重写器的描述，以免内容过于冗长。

> #### 视图
>
> 在PostgreSQL中，[视图](https://www.postgresql.org/docs/current/static/rules-views.html)是基于规则系统实现的。当使用[`CREATE VIEW`](https://www.postgresql.org/docs/current/static/sql-createview.html)命令定义一个视图时，PostgreSQL就会创建相应的规则，并存储到系统目录中。
>
> 假设下面的视图已经被定义，而*pg_rule*中也存储了相应的规则。
>
> ```sql
> sampledb=# CREATE VIEW employees_list 
> sampledb-#   AS SELECT e.id, e.name, d.name AS department 
> sampledb-#      FROM employees AS e, departments AS d WHERE e.department_id = d.id;
> ```
>
> 当执行一个包含该视图的查询，解析器会创建一颗如图3.4(a)所示的语法解析树。
>
> ```sql
> sampledb=# SELECT * FROM employees_list;
> ```
>
> 在该阶段，重写器会基于*pg_rules*中存储的视图规则，将*rangetable*节点重写为一颗子查询对应的语法解析树。
>
> **图3.4 重写阶段一例**
>
> ![rewriter](img/fig-3-04.png)
>
> 因为PostgreSQL使用这种机制来实现视图，因此直至9.2版本，视图都是不能更新的。从9.3版本后可以对视图进行更新；尽管如此，视图的更新仍然存在许多限制，具体细节请参考[官方文档](https://www.postgresql.org/docs/current/static/sql-createview.html#SQL-CREATEVIEW-UPDATABLE-VIEWS)。

### 3.1.4 计划器与执行器

计划器从重写器获取一颗查询树，基于此生成一颗能被**执行器（Executor）**高效执行的（查询）计划树。	

在PostgreSQL中，计划器是完全**基于代价估计（cost-based）**的；它不支持基于规则的优化与**提示（hint）**。计划器是RDBMS中最为复杂的部分，因此本章的后续内容会对计划器做一个概述。

> #### pg_hint_plan
>
> PostgreSQL不支持在SQL中的**提示（hint）**，并且永远也不会去支持。如果你想在查询中使用提示，可以考虑使用*pg_hint_plan*扩展，细节请参考[官方站点](http://pghintplan.osdn.jp/pg_hint_plan.html)。

与其他RDBMS类似，PostgreSQL中的[`EXPLAIN`](https://www.postgresql.org/docs/current/static/sql-explain.html)命令会显示命令的计划树。下面给出了一个具体的例子。

```sql
testdb=# EXPLAIN SELECT * FROM tbl_a WHERE id < 300 ORDER BY data;
                          QUERY PLAN                           
---------------------------------------------------------------
 Sort  (cost=182.34..183.09 rows=300 width=8)
   Sort Key: data
   ->  Seq Scan on tbl_a  (cost=0.00..170.00 rows=300 width=8)
         Filter: (id < 300)
(4 rows)
```

图3.5展示了结果相应的计划树。

**图3.5 一个简单的计划树以及其与EXPLAIN命令的关系**

![planTree](img/fig-3-05.png)



计划树由许多称为**计划节点（plan node）**的元素组成，这些节点挂在*PlannedStmt*结构对应的计划树上。这些元素的定义在[`plannodes.h`](https://github.com/postgres/postgres/blob/master/src/include/nodes/plannodes.h中)中，第3.3.3节与第3.5.4.2会解释相关细节。

每个计划节点都包含着执行器进行处理所必需的信息，在单表查询的场景中，执行器会从终端节点往根节点，依次处理这些节点。

比如图3.5中的计划树就是一个列表，包含一个排序节点和一个顺序扫描节点；因而执行器会首先对表*tbl_a*执行顺序扫描，并对获取的结果进行排序。

执行器会通过[第8章](ch8.md)将阐述的缓冲区管理器来访问数据库集簇的表和索引。当处理一个查询时，执行器会使用预先分配的内存空间，比如*temp_buffers*和*work_mem*，必要时还会创建临时文件。

**图3.6 执行器，缓冲管理器，临时文件之间的关系**

![dd](img/fig-3-06.png)

除此之外，当访问元组的时候，PostgreSQL还会使用并发控制机制来维护运行中事务的一致性和隔离性。[第五章](ch5.md)介绍了并发控制机制。

## 3.2 单表查询的代价估计

PostgreSQL的查询优化是基于**代价（Cost）**的。代价是一个无量纲的值，它并不是一种绝对的性能指标，但可以作为比较各种操作开销时的相对性能指标。

[*costsize.c*](https://github.com/postgres/postgres/blob/master/src/backend/optimizer/path/costsize.c)中的函数用于估算各种操作的代价。所有被执行器执行的操作都有着相应的代价函数。例如，函数`cost_seqscan()` 和 `cost_index()`分别用于估算顺序扫描和索引扫描的代价。

在PostgreSQL中有三种代价：**启动（start-up）** ， **运行（run）**和**总和（total）**。**总代价**是**启动代价**和**运行代价**的和；因此只有启动代价和运行代价是单独估计的。

1. **启动代价（start-up）**：在读取到第一条元组前花费的代价，比如索引扫描节点的**启动代价**就是读取目标表的索引页，取到第一个元组的代价
2. **运行代价（run）**： 获取全部元组的代价
3. **总代价（total）**：前两者之和

[`EXPLAIN`](https://www.postgresql.org/docs/current/static/sql-explain.html)命令显示了每个操作的启动代价和总代价，下面是一个简单的例子：

```sql
testdb=# EXPLAIN SELECT * FROM tbl;
                       QUERY PLAN                        
---------------------------------------------------------
 Seq Scan on tbl  (cost=0.00..145.00 rows=10000 width=8)
(1 row)
```

在第4行显示了顺序扫描的相关信息。代价部分包含了两个值：0.00和145.00。在本例中，启动代价和总代价分别为0.00和145.00。

在本节中，我们将详细介绍顺序扫描，索引扫描和排序操作的代价是如何估算的。

在接下来的内容中，我们使用下面这个表及其索引作为例子。

```sql
testdb=# CREATE TABLE tbl (id int PRIMARY KEY, data int);
testdb=# CREATE INDEX tbl_data_idx ON tbl (data);
testdb=# INSERT INTO tbl SELECT generate_series(1,10000),generate_series(1,10000);
testdb=# ANALYZE;
testdb=# \d tbl
      Table "public.tbl"
 Column |  Type   | Modifiers 
--------+---------+-----------
 id     | integer | not null
 data   | integer | 
Indexes:
    "tbl_pkey" PRIMARY KEY, btree (id)
    "tbl_data_idx" btree (data)
```

### 3.2.1 顺序扫描

顺序扫描的代价是通过函数`cost_seqscan()`估计的。本节将研究顺序扫描代价是如何估计的，以下列查询为例。

```sql
testdb=# SELECT * FROM tbl WHERE id < 8000;
```

在顺序扫描中，启动代价等于0，而运行代价由以下公式定义：
$$
\begin{align}
  \verb|run_cost| 
  &= \verb|cpu_run_cost| + \verb|disk_run_cost | \\
  &= (\verb|cpu_tuple_cost| + \verb|cpu_operator_cost|) × N_{\verb|tuple|} + \verb|seq_page_cost| × N_{\verb|page|},
\end{align}
$$
其中[*seq_page_cost*](https://www.postgresql.org/docs/current/static/runtime-config-query.html#GUC-SEQ-PAGE-COST)，[*cpu_tuple_cost*](https://www.postgresql.org/docs/current/static/runtime-config-query.html#GUC-CPU-TUPLE-COST)和[*cpu_operator_cost*](https://www.postgresql.org/docs/current/static/runtime-config-query.html#GUC-CPU-OPERATOR-COST)是在*postgresql.conf* 中配置的参数，默认值分别为1.0，0.01和0.0025。$N_{tuple}$ 和$N_{page}$ 分别是表中的元组总数和页面总数，这两个值可以使用下列查询获得。

```sql
testdb=# SELECT relpages, reltuples FROM pg_class WHERE relname = 'tbl';
 relpages | reltuples 
----------+-----------
       45 |     10000
(1 row)
```

$$
\begin{equation}\tag{1}
	N_{\verb|tuple|}=10000
\end{equation}
$$

$$
\begin{equation}\tag{2}
	N_{\verb|page|}=45
\end{equation}
$$

因此：
$$
\begin{align}
 \verb|run_cost| 
 	    &= (0.01 + 0.0025) × 10000 + 1.0 × 45 = 170.0.
\end{align}
$$

最终：
$$
\verb|total_cost| = 0.0 + 170.0 = 170.0
$$


作为验证，下面是该查询的`EXPLAIN`结果：

```sql
testdb=# EXPLAIN SELECT * FROM tbl WHERE id < 8000;
                       QUERY PLAN                       
--------------------------------------------------------
 Seq Scan on tbl  (cost=0.00..170.00 rows=8000 width=8)
   Filter: (id < 8000)
(2 rows)
```

在第4行中可以看到，启动代价和总代价分别是0.00和170.0，且预计全表扫描返回行数为8000条（元组）。

在第5行显示了一个顺序扫描的过滤器`Filter:(id < 8000)`。更精确地说，它是一个**表级过滤谓词（table level filter predicate）**。注意这种类型的过滤器只会在读取所有元组的时候使用，它并不会减少需要扫描的表页面数量。

> 从优化运行代价的角度来看，PostgreSQL假设所有的物理页都是从存储介质中获取的；即，PostgreSQL不会考虑扫 描的页面是否来自共享缓冲区。

### 3.2.2 索引扫描

尽管PostgreSQL支持很多[索引方法](https://www.postgresql.org/docs/current/static/indexes-types.html)，比如B树，[GiST](https://www.postgresql.org/docs/current/static/gist.html)，[GIN](https://www.postgresql.org/docs/current/static/gin.html)和[BRIN](https://www.postgresql.org/docs/current/static/brin.html)，不过索引扫描的代价估计都使用一个共用的代价函数：`cost_index()`。

在这一节中，我们基于下面的查询，探究索引扫描的代价估计：

本节将研究索引扫描的代价是如何估计的，以下列查询为例。

```sql
testdb=# SELECT id, data FROM tbl WHERE data < 240;
```

在估计该代价之前，下面的查询能获取$N_{\verb|index|,\verb|page|}$和$N_{\verb|index|,\verb|tuple|}$的值：

```sql
testdb=# SELECT relpages, reltuples FROM pg_class WHERE relname = 'tbl_data_idx';
 relpages | reltuples 
----------+-----------
       30 |     10000
(1 row)
```

$$
\begin{equation}\tag{3}
	N_{\verb|index|,\verb|tuple|} = 10000
\end{equation}
$$

$$
\begin{equation}\tag{4}
	N_{\verb|index|,\verb|page|} = 30
\end{equation}
$$

#### 3.2.2.1 启动代价

索引扫描的启动代价就是读取索引页以访问目标表的第一条元组的代价，由下面的公式定义：
$$
\begin{equation}
\verb| start-up_cost| = \{\mathrm{ceil}(\log_2 (N_{\verb|index|,\verb|tuple|}))
		 + (H_{\verb|index|} + 1) × 50\} × \verb|cpu_operator_cost|
\end{equation}
$$
其中$H_{index}$是索引树的高度。

在本例中，套用公式(3)，$N_{index,tuple}$是10000；$H_{index}$是1；$\verb|cpu_operator_cost|$是0.0025（默认值）。因此
$$
\begin{equation}\tag{5}
 \verb|start-up_cost| = \{\mathrm{ceil}(\log_2(10000)) + (1 + 1) × 50\} × 0.0025 = 0.285
\end{equation}
$$
#### 3.2.2.2 运行代价

索引扫描的运行代价是表和索引的CPU代价与IO代价之和。
$$
\begin{align}
 \verb|run_cost| &= (\verb|index_cpu_cost| + \verb|table_cpu_cost|) 
 	    	  + (\verb|index_io_cost| + \verb|table_io_cost|).
\end{align}
$$

> 如果使用[仅索引扫描](https://www.postgresql.org/docs/10/static/indexes-index-only-scans.html)，则不会估计`table_cpu_cost`与`table_io_cost`，仅索引扫描将在[第七章](ch7.md)中介绍。

前三个代价（即`index_cpu_cost`，`table_cpu_cost`和`index_io_cost`）如下所示：

$$
\begin{align}
 \verb|index_cpu_cost| &= \verb|Selectivity| × N_{\verb|index|,\verb|tuple|} × (\verb|cpu_index_tuple_cost| + \verb|qual_op_cost|) \\
 \verb|table_cpu_cost| &= \verb|Selectivity| × N_{\verb|tuple|}× \verb|cpu_tuple_cost| \\
 \verb|index_io_cost|   &= \mathrm{ceil}(\verb|Selectivity| × N_{\verb|index|,\verb|page|}) ×\verb|random_page_cost|
\end{align}
$$


以上公式中的[`cpu_index_tuple_cost`](https://www.postgresql.org/docs/current/static/runtime-config-query.html#GUC-CPU-INDEX-TUPLE-COST)和[`random_page_cost`](https://www.postgresql.org/docs/current/static/runtime-config-query.html#GUC-RANDOM-PAGE-COST)在*postgresql.conf*中配置（默认值分别为0.005和4.0）。$\verb|qual_op_cost|$粗略来说就是索引求值的代价，默认值是0.0025，这里不再展开。**选择率（Selectivity）**是一个0到1之间的浮点数，代表查询指定的`WHERE`子句在索引中搜索范围的比例。举个例子，$(\verb|Selectivity| × N_{\verb|tuple|})$就是需要读取的表元组数量，$(\verb|Selectivity| × N_{\verb|index|,\verb|tuple|})$就是需要读取的索引元组数量，诸如此类。

> #### 选择率（Selectivity）
>
> 查询谓词的选择率是通过**直方图界值（histogram_bounds）**和**众数（Most Common Value, MCV）**估计的，这些信息都存储在*pg_stats* 中。这里通过一个特定的例子来简要介绍选择率的计算方法，细节可以参考[官方文档](https://www.postgresql.org/docs/10/static/row-estimation-examples.html)。
>
> 表中每一列的MCV都在*pg_stats*视图的*most_common_vals* 和 *most_common_freqs*中成对存储。
>
> + **众数（most_common_vals）**：该列上的众数列表
> + **众数频率（most_common_freqs）**：MCV相应的频率列表
>
> 下面是一个简单的例子。表*countries*有两列：一列*country*存储国家名，一列`continent`存储该国所属大洲。
>
> ```sql
> testdb=# \d countries
>    Table "public.countries"
>   Column   | Type | Modifiers 
> -----------+------+-----------
>  country   | text | 
>  continent | text | 
> Indexes:
>     "continent_idx" btree (continent)
> 
> testdb=# SELECT continent, count(*) AS "number of countries", 
> testdb-#     (count(*)/(SELECT count(*) FROM countries)::real) AS "number of countries / all countries"
> testdb-#       FROM countries GROUP BY continent ORDER BY "number of countries" DESC;
>    continent   | number of countries | number of countries / all countries 
> ---------------+---------------------+-------------------------------------
>  Africa        |                  53 |                   0.274611398963731
>  Europe        |                  47 |                   0.243523316062176
>  Asia          |                  44 |                   0.227979274611399
>  North America |                  23 |                   0.119170984455959
>  Oceania       |                  14 |                  0.0725388601036269
>  South America |                  12 |                  0.0621761658031088
> (6 rows)
> ```
>
> 考虑下面的查询，该查询带有`WHERE`条件`continent = 'Asia'`。
>
> ```sql
> testdb=# SELECT * FROM countries WHERE continent = 'Asia';
> ```
>
> 这时候，计划器使用*continent*列上的MCV来估计索引扫描的代价，列上的*most_common_vals*与 *most_common_freqs* 如下所示：
>
> ```sql
> testdb=# \x
> Expanded display is on.
> testdb=# SELECT most_common_vals, most_common_freqs FROM pg_stats 
> testdb-#                  WHERE tablename = 'countries' AND attname='continent';
> -[ RECORD 1 ]-----+-------------------------------------------------------------
> most_common_vals  | {Africa,Europe,Asia,"North America",Oceania,"South America"}
> most_common_freqs | {0.274611,0.243523,0.227979,0.119171,0.0725389,0.0621762}
> ```
>
> 与*most_common_vals*中*Asia*值对应的*most_common_freqs*为0.227979。因此，0.227979会在估算中被用作选择率。
>
> 如果MCV不可用，就会使用目标列上的直方图界值来估计代价。
>
> + **直方图值（histogram_bounds）**是一系列值，这些值将列上的取值划分为数量大致相同的若干个组。
>
> 下面是一个具体的例子。这是表*tbl*中*data*列上的直方图界值；
>
> ```sql
> testdb=# SELECT histogram_bounds FROM pg_stats WHERE tablename = 'tbl' AND attname = 'data';
>         			     	      histogram_bounds
> ------------------------------------------------------------------------------------
>  {1,100,200,300,400,500,600,700,800,900,1000,1100,1200,1300,1400,1500,1600,1700,1800,1900,2000,2100,
> 2200,2300,2400,2500,2600,2700,2800,2900,3000,3100,3200,3300,3400,3500,3600,3700,3800,3900,4000,4100,
> 4200,4300,4400,4500,4600,4700,4800,4900,5000,5100,5200,5300,5400,5500,5600,5700,5800,5900,6000,6100,
> 6200,6300,6400,6500,6600,6700,6800,6900,7000,7100,7200,7300,7400,7500,7600,7700,7800,7900,8000,8100,
> 8200,8300,8400,8500,8600,8700,8800,8900,9000,9100,9200,9300,9400,9500,9600,9700,9800,9900,10000}
> (1 row)
> ```
>
> 默认情况下，直方图界值会将列上的取值划分入100个桶。图3.7展示了这些桶及其对应的直方图界值。桶从0开始编号，每个桶保存了（大致）相同数量的元组。直方图界值就是相应桶的边界。比如，直方图界值的第0个值是1，意即这是*bucket_0*中的最小值。第1个值是100，意即*bucket_1*中的最小值是100，等等。
>
> **图3.7 桶和直方图界值**
>
> ![](img/fig-3-07.png)
>
> 然后本节例子中选择率计算如下所示。假设查询带有`WHERE`子句`data < 240`，而值240落在第二个桶中。在本例中可以通过线性插值推算出相应的选择率。因此查询中*data*列的选择率可以套用下面的公式计算：
> $$
> \verb|Selectivity| = \frac{2+(240-hb[2])/(hb[3]-hb[2])}{100}=\frac{2+(240-200)/(300-200)}{100}=\frac{2+40/100}{100}=0.024    \ (6)
> $$
>

因此，根据公式(1)，(3)，(4)和(6)，有
$$
\begin{equation}\tag{7}
	\verb|index_cpu_cost| = 0.024× 10000 × (0.005+0.0025)=1.8
\end{equation}
$$
$$
\begin{equation}\tag{8}
\verb|table_cpu_cost| = 0.024 × 10000 × 0.01 = 2.4
\end{equation}
$$

$$
\begin{equation}\tag{9}
\verb|index_io_cost| = ceil(0.024 × 30) × 4.0 = 4.0
\end{equation}
$$



$\verb|table_io_cost|$ 由下面的公式定义：
$$
\begin{equation}
\verb|table_io_cost| = \verb|max_io_cost| + \verb|indexCorerelation|^2 × (\verb|min_io_cost|-\verb|max_io_cost|)
\end{equation}
$$

$\verb|max_io_cost_io_cost|$ 是最差情况下的I/O代价，即，随机扫描所有数据页的代价；这个代价由以下公式定义：
$$
\begin{equation}
\verb|max_io_cost| = N_{\verb|page|} × \verb|random_page_cost|
\end{equation}
$$

在本例中，由(2)，$N_{\verb|page|}=45$，得
$$
\begin{equation}\tag{10}
\verb|max_io_cost| = 45 × 4.0 = 180.0
\end{equation}
$$

$\verb|min_io_cost|$是最优情况下的I/O代价，即，顺序扫描选定的数据页；这个代价由以下公式定义：
$$
\begin{equation}
\verb|min_io_cost| = 1 × \verb|random_page_cost| + (\mathrm{ceil}(\verb|Selectivity| × N_{\verb|page|})-1) × \verb|seq_page_cost|
\end{equation}
$$
在本例中，
$$
\begin{equation} \tag{11}
\verb|min_io_cost| \ = 1 × 4.0 + (\mathrm{ceil}(0.024 × 45)-1) × 1.0
\end{equation}
$$

下文详细介绍$\verb|indexCorrelation|$，在本例中，
$$
\begin{equation} \tag{12}
	\verb|indexCorrelation| = 1.0
\end{equation}
$$

由(10)，(11)和(12)，得
$$
\begin{equation} \tag{13}
\verb|table_io_cost| = 180.0+1.0^2 × (5.0-180.0)=5.0
\end{equation}
$$

综上，由(7)，(8)，(9)和(13)得
$$
\begin{equation}\tag{14}
\verb|run_cost| = (1.8+2.4)+(4.0+5.0)=13.2
\end{equation}
$$

> ##### 索引相关性（index correlation）
>
> 索引相关性是列值在物理上的顺序和逻辑上的顺序的统计相关性（引自官方文档）。索引相关性的取值范围从$-1$到$+1$。下面的例子有助于理解索引扫描和索引相关性的关系。
>
> 表*tbl_corr*有5个列：两个列为文本类型，三个列为整数类型。这三个整数列保存着从1到12的数字。物理上，表*tbl_corr*包含三个页，每个页有4个元组。每个数字列有一个名如*index_col_asc*的索引。
>
> ```sql
> testdb=# \d tbl_corr
>     Table "public.tbl_corr"
>   Column  |  Type   | Modifiers 
> ----------+---------+-----------
>  col      | text    | 
>  col_asc  | integer | 
>  col_desc | integer | 
>  col_rand | integer | 
>  data     | text    |
> Indexes:
>     "tbl_corr_asc_idx" btree (col_asc)
>     "tbl_corr_desc_idx" btree (col_desc)
>     "tbl_corr_rand_idx" btree (col_rand)
> ```
>
> ```sql
> testdb=# SELECT col,col_asc,col_desc,col_rand 
> testdb-#                         FROM tbl_corr;
>    col    | col_asc | col_desc | col_rand 
> ----------+---------+----------+----------
>  Tuple_1  |       1 |       12 |        3
>  Tuple_2  |       2 |       11 |        8
>  Tuple_3  |       3 |       10 |        5
>  Tuple_4  |       4 |        9 |        9
>  Tuple_5  |       5 |        8 |        7
>  Tuple_6  |       6 |        7 |        2
>  Tuple_7  |       7 |        6 |       10
>  Tuple_8  |       8 |        5 |       11
>  Tuple_9  |       9 |        4 |        4
>  Tuple_10 |      10 |        3 |        1
>  Tuple_11 |      11 |        2 |       12
>  Tuple_12 |      12 |        1 |        6
> (12 rows)
> ```
>
> 这些列的索引相关性如下：
>
> ```sql
> testdb=# SELECT tablename,attname, correlation FROM pg_stats WHERE tablename = 'tbl_corr';
>  tablename | attname  | correlation 
> -----------+----------+-------------
>  tbl_corr  | col_asc  |           1
>  tbl_corr  | col_desc |          -1
>  tbl_corr  | col_rand |    0.125874
> (3 rows)
> ```
>
> 当执行下列查询时，由于所有的目标元组都在第一页中，PostgreSQL只会读取第一页，如图3.8(a)所示。
>
> ```sql
> testdb=# SELECT * FROM tbl_corr WHERE col_asc BETWEEN 2 AND 4;
> ```
>
> 而执行下列查询时则不然，PostgreSQL需要读所有的页，如图3.8(b)所示。
>
> ```sql
> testdb=# SELECT * FROM tbl_corr WHERE col_rand BETWEEN 2 AND 4;
> ```
>
> 如此可知，索引相关性是一种统计上的相关性。在索引扫描代价估计中，索引相关性体现了索引顺序和物理元组顺序扭曲程度给随机访问性能造成的影响大小。
>
> **图3.8 索引相关性**
>
> ![indexcor](img/fig-3-08.png)

#### 3.2.2.3 整体代价

由(3)和(14)可得
$$
\begin{equation}\tag{15}
	\verb|total_cost| = 0.285 + 13.2 = 13.485
\end{equation}
$$

作为确认，上述`SELECT`查询的`EXPLAIN`结果如下所示：

```sql
testdb=# EXPLAIN SELECT id, data FROM tbl WHERE data < 240;
                                QUERY PLAN                                 
---------------------------------------------------------------------------
 Index Scan using tbl_data_idx on tbl  (cost=0.29..13.49 rows=240 width=8)
   Index Cond: (data < 240)
(2 rows)
```

在第4行可以看到启动代价和总代价分别是0.29和13.49，预估有240条元组被扫描。

在第5行显示了一个索引条件`Index Cond:(data < 240)`。更准确地说，这个条件叫做**访问谓词（access predicate）**，它表达了索引扫描的开始条件与结束条件。

> 根据[这篇文章](https://use-the-index-luke.com/sql/explain-plan/postgresql/filter-predicates)，PostgreSQL中的`EXPLAIN`命令不会区分**访问谓词（access predicate）**和**索引过滤谓词（index filter predicate）**。因此当分析`EXPLAIN`的输出时，即使看到了“IndexCond”，也应当注意一下预估返回行数。

> ##### seq_page_cost和random_page_cost
>
> [seq_page_cost](https://www.postgresql.org/docs/10/static/runtime-config-query.html#GUC-SEQ-PAGE-COST)和[random_page_cost](https://www.postgresql.org/docs/10/static/runtime-config-query.html#GUC-RANDOM-PAGE-COST)的默认值分别为1.0和4.0。这意味着PostgreSQL假设随机扫描比顺序扫描慢4倍；显然，PostgreSQL的默认值是基于HDD（普通硬盘）设置的。
>
> 另一方面，近年来SSD得到了广泛的应用，`random_page_cost`的默认值就显得太大了。使用SSD时如果仍然采用`random_page_cost`的默认值，则计划器有可能会选择低效的计划。因此当使用SSD时最好将`random_page_cost`的值设为1.0。
>
> [这篇文章](https://amplitude.engineering/how-a-single-postgresql-config-change-improved-slow-query-performance-by-50x-85593b8991b0)报告了使用`random_page_cost`默认值导致的问题。

### 3.2.3 排序

**排序路径（sort path）** 会在排序操作中被使用。排序操作包括`ORDER BY`，归并连接的预处理操作，以及其他函数。函数`cost_sort()`用于估计排序操作的代价。

如果能在工作内存中放下所有元组，那么排序操作会选用快速排序算法。否则的话则会创建临时文件，使用文件归并排序算法。

排序路径的启动代价就是对目标表的排序代价，因此代价就是$O(N_{\verb|sort|}× \log_2(N_{\verb|sort|})$，这里$N_{\verb|sort|}$就是待排序的元组数。排序路径的运行代价就是读取已经排好序的元组的代价，因而代价就是$O(N_{sort})$。

本节将研究以下查询排序代价的估计过程。假设该查询只使用工作内存，不使用临时文件。

```sql
testdb=# SELECT id, data FROM tbl WHERE data < 240 ORDER BY id;
```

在本例中，启动代价由以下公式定义：
$$
\begin{equation}
\verb|start-up_cost| = \verb|C|+ \verb|comparison_cost| × N_{\verb|sort|} × \log_2(N_{\verb|sort|})
\end{equation}
$$

这里$C$就是上一次扫描的总代价，即上次索引扫描的总代价；由(15)可得C等于13.485；$N_{\verb|sort|}=240$；$\verb|comparison_cost|$ 定义为$2 × \verb|cpu_operator_cost|$。因此有

$$
\begin{equation}
\verb|start-up_cost| = 13.485+(2×0.0025)×240.0×\log_2(240.0)=22.973
\end{equation}
$$

运行代价是在内存中读取排好序的元组的代价，即：
$$
\begin{equation}
\verb|run_cost| = \verb|cpu_operator_cost| × N_{\verb|sort|} = 0.0025 × 240 = 0.6
\end{equation}
$$
综上：
$$
\begin{equation}
\verb|total_cost|=22.973+0.6=23.573
\end{equation}
$$
作为确认，以上`SELECT`查询的`EXPLAIN`命令结果如下：

```sql
testdb=# EXPLAIN SELECT id, data FROM tbl WHERE data < 240 ORDER BY id;
                                   QUERY PLAN                                    
---------------------------------------------------------------------------------
 Sort  (cost=22.97..23.57 rows=240 width=8)
   Sort Key: id
   ->  Index Scan using tbl_data_idx on tbl  (cost=0.29..13.49 rows=240 width=8)
         Index Cond: (data < 240)
(4 rows)
```

在第4行可以看到启动代价和运行代价分别为22.97和23.57。




## 3.3 创建单表查询的计划树

计划器非常复杂，故本节仅描述最简单的情况，即单表查询的计划树创建过程。更复杂的查询，换而言之即多表查询，其计划树创建过程将在第3.6节中阐述。

PostgreSQL中的计划器会执行三个处理步骤：

1. 执行预处理
2. 在所有可能的访问路径中，找出代价最小的访问路径
3. 按照代价最小的路径，创建计划树

**访问路径（access path）**是估算代价时的处理单元；比如，顺序扫描，索引扫描，排序以及各种连接操作都有其对应的**路径**。访问路径只在计划器创建查询计划树的时候使用。最基本的访问路径数据结构就是[relation.h](https://github.com/postgres/postgres/blob/master/src/include/nodes/relation.h)中定义的*Path*结构体。它就相当于是顺序扫描。所有其他的访问路径都基于该结构，下面会介绍细节。

计划器为了处理上述步骤，会在内部创建一个`PlannerInfo`数据结构。在该数据结构中包含着查询树，查询所涉及关系信息，访问路径等等。

```c
typedef struct PathKey
{
    NodeTag type;
    EquivalenceClass *pk_eclass; /* 值是否有序 */
    Oid pk_opfamily;             /* 用于定义顺序的B树操作符族 */
    int pk_strategy;             /* 排序方向(ASC or DESC) */
    bool pk_nulls_first;         /* NULL是否排序在常规值之前？ */
} PathKey;

typedef struct Path
{
    NodeTag type;
    NodeTag pathtype;          /* 标识 scan/join 方法的标签 */
    RelOptInfo *parent;        /* 路径所基于的关系 */
    PathTarget *pathtarget;    /* Vars/Exprs的列表, 代价, 宽度 */
    ParamPathInfo *param_info; /* 参数化信息, 如果没有则为NULL */
    bool parallel_aware;       /* 涉及到并行相关的逻辑？ */
    bool parallel_safe;        /* 是否能作为并行执行计划的一部分? */
    int parallel_workers;      /* 期待的并行工作进程数量； 0表示没有并行 */

    /* 估计路径的尺寸或代价 (更多详情参考costsize.c) */
    double rows;       /* 预估结果元组数目 */
    Cost startup_cost; /* 获取任何元组前需要花费的代价 */
    Cost total_cost;   /* 总代价 (假设获取所有元组所需代价) */
    List *pathkeys;    /* 路径输出的排序顺序 */
    /* pathkeys 是PathKey节点的列表，PathKey定义见上面 */
} Path;

typedef struct PlannerInfo
{
    NodeTag type;
    Query *parse;                    /* 被计划的查询 */
    PlannerGlobal *glob;             /* 当前计划器运行时的全局信息 */
    Index query_level;               /* 最外层查询为1 */
    struct PlannerInfo *parent_root; /* 最外层查询为NULL */

    /*
	 * plan_params contains the expressions that this query level needs to
	 * make available to a lower query level that is currently being planned.
	 * outer_params contains the paramIds of PARAM_EXEC Params that outer
	 * query levels will make available to this query level.
	 */
    List *plan_params; /* PlannerParamItems的列表, 见下 */
    Bitmapset *outer_params;

    /*
	 * simple_rel_array holds pointers to "base rels" and "other rels" (see
	 * comments for RelOptInfo for more info).  It is indexed by rangetable
	 * index (so entry 0 is always wasted).  Entries can be NULL when an RTE
	 * does not correspond to a base relation, such as a join RTE or an
	 * unreferenced view RTE; or if the RelOptInfo hasn't been made yet.
	 */
    struct RelOptInfo **simple_rel_array; /* All 1-rel RelOptInfos */
    int simple_rel_array_size;            /* allocated size of array */

    /*
	 * simple_rte_array is the same length as simple_rel_array and holds
	 * pointers to the associated rangetable entries.  This lets us avoid
	 * rt_fetch(), which can be a bit slow once large inheritance sets have
	 * been expanded.
	 */
    RangeTblEntry **simple_rte_array; /* rangetable as an array */

    /*
	 * all_baserels is a Relids set of all base relids (but not "other"
	 * relids) in the query; that is, the Relids identifier of the final join
	 * we need to form.  This is computed in make_one_rel, just before we
	 * start making Paths.
	 */
    Relids all_baserels;

    /*
	 * nullable_baserels is a Relids set of base relids that are nullable by
	 * some outer join in the jointree; these are rels that are potentially
	 * nullable below the WHERE clause, SELECT targetlist, etc.  This is
	 * computed in deconstruct_jointree.
	 */
    Relids nullable_baserels;

    /*
	 * join_rel_list is a list of all join-relation RelOptInfos we have
	 * considered in this planning run.  For small problems we just scan the
	 * list to do lookups, but when there are many join relations we build a
	 * hash table for faster lookups.  The hash table is present and valid
	 * when join_rel_hash is not NULL.  Note that we still maintain the list
	 * even when using the hash table for lookups; this simplifies life for
	 * GEQO.
	 */
    List *join_rel_list;        /* list of join-relation RelOptInfos */
    struct HTAB *join_rel_hash; /* optional hashtable for join relations */

    /*
	 * When doing a dynamic-programming-style join search, join_rel_level[k]
	 * is a list of all join-relation RelOptInfos of level k, and
	 * join_cur_level is the current level.  New join-relation RelOptInfos are
	 * automatically added to the join_rel_level[join_cur_level] list.
	 * join_rel_level is NULL if not in use.
	 */
    List **join_rel_level;    /* lists of join-relation RelOptInfos */
    int join_cur_level;       /* index of list being extended */
    List *init_plans;         /* init SubPlans for query */
    List *cte_plan_ids;       /* per-CTE-item list of subplan IDs */
    List *multiexpr_params;   /* List of Lists of Params for MULTIEXPR subquery outputs */
    List *eq_classes;         /* list of active EquivalenceClasses */
    List *canon_pathkeys;     /* list of "canonical" PathKeys */
    List *left_join_clauses;  /* list of RestrictInfos for
					 * mergejoinable outer join clauses w/nonnullable var on left */
    List *right_join_clauses; /* list of RestrictInfos for
					 * mergejoinable outer join clauses w/nonnullable var on right */
    List *full_join_clauses;  /* list of RestrictInfos for mergejoinable full join clauses */
    List *join_info_list;     /* list of SpecialJoinInfos */
    List *append_rel_list;    /* list of AppendRelInfos */
    List *rowMarks;           /* list of PlanRowMarks */
    List *placeholder_list;   /* list of PlaceHolderInfos */
    List *fkey_list;          /* list of ForeignKeyOptInfos */
    List *query_pathkeys;     /* desired pathkeys for query_planner() */
    List *group_pathkeys;     /* groupClause pathkeys, if any */
    List *window_pathkeys;    /* pathkeys of bottom window, if any */
    List *distinct_pathkeys;  /* distinctClause pathkeys, if any */
    List *sort_pathkeys;      /* sortClause pathkeys, if any */
    List *initial_rels;       /* RelOptInfos we are now trying to join */

    /* Use fetch_upper_rel() to get any particular upper rel */
    List *upper_rels[UPPERREL_FINAL + 1]; /* upper-rel RelOptInfos */

    /* Result tlists chosen by grouping_planner for upper-stage processing */
    struct PathTarget *upper_targets[UPPERREL_FINAL + 1];

    /*
	 * grouping_planner passes back its final processed targetlist here, for
	 * use in relabeling the topmost tlist of the finished Plan.
	 */
    List *processed_tlist;

    /* Fields filled during create_plan() for use in setrefs.c */
    AttrNumber *grouping_map;    /* for GroupingFunc fixup */
    List *minmax_aggs;           /* List of MinMaxAggInfos */
    MemoryContext planner_cxt;   /* context holding PlannerInfo */
    double total_table_pages;    /* # of pages in all tables of query */
    double tuple_fraction;       /* tuple_fraction passed to query_planner */
    double limit_tuples;         /* limit_tuples passed to query_planner */
    bool hasInheritedTarget;     /* true if parse->resultRelation is an inheritance child rel */
    bool hasJoinRTEs;            /* true if any RTEs are RTE_JOIN kind */
    bool hasLateralRTEs;         /* true if any RTEs are marked LATERAL */
    bool hasDeletedRTEs;         /* true if any RTE was deleted from jointree */
    bool hasHavingQual;          /* true if havingQual was non-null */
    bool hasPseudoConstantQuals; /* true if any RestrictInfo has pseudoconstant = true */
    bool hasRecursion;           /* true if planning a recursive WITH item */

    /* These fields are used only when hasRecursion is true: */
    int wt_param_id;                 /* PARAM_EXEC ID for the work table */
    struct Path *non_recursive_path; /* a path for non-recursive term */

    /* These fields are workspace for createplan.c */
    Relids curOuterRels;  /* outer rels above current node */
    List *curOuterParams; /* not-yet-assigned NestLoopParams */

    /* optional private data for join_search_hook, e.g., GEQO */
    void *join_search_private;
} PlannerInfo;
```

本节会通过一个具体的例子，来描述如何基于查询树创建计划树。

### 3.3.1 预处理

在创建计划树之前，计划器对先`PlannerInfo`中的查询树进行一些预处理。

预处理有很多步骤，本节只讨论和单表查询处理相关的主要步骤。其他预处理操作将在3.6节中描述。

1. 简化**目标列表（target list）**，`LIMIT`子句等；

   例如，表达式`2+2`会被重写为`4`，这是由[`clauses.c`](https://github.com/postgres/postgres/blob/master/src/backend/optimizer/util/clauses.c)中`eval_const_expressions()`函数负责的。

2. 布尔表达式的规范化

   例如，`NOT(NOT a)`会被重写为`a`

3. 压平与/或表达式

   SQL标准中的AND/OR是二元操作符；但在PostgreSQL内部它们是多元操作符。而计划器总是会假设所有的嵌套AND/OR都应当被压平。

   这里有一个具体的例子。考虑这样一个布尔表达式`(id = 1) OR (id = 2) OR (id = 3)`，图3.9(a) 展示了使用二元表达式时的查询树，预处理会将这些二元算子简化压平为一个三元算子，如图3.9(b)所示。

   **图3.9. 压平布尔表达式的例子**

   ![扁平化](img/fig-3-09.png)

### 3.3.2 找出代价最小的访问路径

计划器对所有可能的访问路径进行代价估计，然后选择代价最小的那个。具体来说，计划器会执行以下几个步骤：

1. 创建一个`RelOptInfo`数据结构，存储访问路径及其代价。

   `RelOptInfo`结构体是通过`make_one_rel()`函数创建的，并存储于`PlannerInfo`结构体的`simple_rel_array`字段中，如图3.10所示。在初始状态时`RelOptInfo`持有着`baserestrictinfo`变量，如果存在相应索引，还会持有`indexlist`变量。`baserestrictinfo`存储着查询的`WHERE子`句，而`indexlist`存储着目标表上相关的索引。

   ```c
   typedef enum RelOptKind
   {
   	RELOPT_BASEREL,
   	RELOPT_JOINREL,
   	RELOPT_OTHER_MEMBER_REL,
   	RELOPT_UPPER_REL,
   	RELOPT_DEADREL
   } RelOptKind;
   
   typedef struct RelOptInfo
   {
   	NodeTag		type;
   	RelOptKind	reloptkind;
   
   	/* 本RelOptInfo包含的所有关系 */
   	Relids		relids;			/* set of base relids (rangetable indexes) */
   
   	/* size estimates generated by planner */
   	double		rows;			/* estimated number of result tuples */
   
   	/* per-relation planner control flags */
   	bool		consider_startup;	/* keep cheap-startup-cost paths? */
   	bool		consider_param_startup; /* ditto, for parameterized paths? */
   	bool		consider_parallel;	/* consider parallel paths? */
   
   	/* default result targetlist for Paths scanning this relation */
   	struct PathTarget *reltarget;		/* list of Vars/Exprs, cost, width */
   
   	/* materialization information */
   	List	   *pathlist;			/* Path structures */
   	List	   *ppilist;			/* ParamPathInfos used in pathlist */
   	List	   *partial_pathlist;		/* partial Paths */
   	struct Path *cheapest_startup_path;
   	struct Path *cheapest_total_path;
   	struct Path *cheapest_unique_path;
   	List	   *cheapest_parameterized_paths;
   
   	/* parameterization information needed for both base rels and join rels */
   	/* (see also lateral_vars and lateral_referencers) */
   	Relids		direct_lateral_relids;	/* rels directly laterally referenced */
   	Relids		lateral_relids; 	/* minimum parameterization of rel */
   
   	/* information about a base rel (not set for join rels!) */
   	Index		relid;
   	Oid		reltablespace;		/* containing tablespace */
   	RTEKind		rtekind;		/* RELATION, SUBQUERY, or FUNCTION */
   	AttrNumber	min_attr;		/* smallest attrno of rel (often <0) */
   	AttrNumber	max_attr;		/* largest attrno of rel */
   	Relids	   	*attr_needed;		/* array indexed [min_attr .. max_attr] */
   	int32	   	*attr_widths;	   	/* array indexed [min_attr .. max_attr] */
   	List	   	*lateral_vars;	   	/* LATERAL Vars and PHVs referenced by rel */
   	Relids		lateral_referencers;	/* rels that reference me laterally */
   	List	   	*indexlist;		/* list of IndexOptInfo */
   	BlockNumber 	pages;			/* size estimates derived from pg_class */
   	double		tuples;
   	double		allvisfrac;
   	PlannerInfo 	*subroot;		/* if subquery */
   	List	   	*subplan_params; 	/* if subquery */
   	int		rel_parallel_workers;	/* wanted number of parallel workers */
   
   	/* Information about foreign tables and foreign joins */
   	Oid		serverid;		/* identifies server for the table or join */
   	Oid		userid;			/* identifies user to check access as */
   	bool		useridiscurrent;	/* join is only valid for current user */
   	/* use "struct FdwRoutine" to avoid including fdwapi.h here */
   	struct FdwRoutine *fdwroutine;
   	void	   	*fdw_private;
   
   	/* 被各种扫描与连接所使用 */
   	List	   	*baserestrictinfo;	/* RestrictInfo结构体列表 (如果存在基础关系) */
   	QualCost	baserestrictcost;	/* 求值上述限制条件的代价 */
   	List	   	*joininfo;		/* RestrictInfo 结构体列表，涉及到本表的连接会用到 */
   	bool		has_eclass_joins;	/* T 意味着joininfo不完整 */
   } RelOptInfo;
   ```

2. 估计所有可能访问路径的代价，并将访问路径添加至`RelOptInfo`结构中。

   这一处理过程的细节如下：

   1.  创建一条路径，估计该路径中顺序扫描的代价，并将其写入路径中。将该路径添加到`RelOptInfo`结构的`pathlist`变量中。
   2. 如果目标表上存在相关的索引，则为每个索引创建相应的索引访问路径。估计所有索引扫描的代价，并将代价写入相应路径中。然后将索引访问路径添加到`pathlist`变量中。
   3. 如果可以进行[位图扫描](https://wiki.postgresql.org/wiki/Bitmap_Indexes)，则创建一条位图扫描访问路径。估计所有的位图扫描的代价，并将代价写入到路径中。然后将位图扫描路径添加到`pathlist`变量中。

3. 从`RelOptInfo`的`pathlist`中，找出代价最小的访问路径。

4. 如有必要，估计`LIMIT`，`ORDER BY`和`AGGREGATE`操作的代价。

为了更加清晰的理解计划器的执行过程，下面给出了两个具体的例子。

#### 3.3.2.1 例1

首先来研究一个不带索引的简单单表查询；该查询同时包含`WHERE`和`ORDER BY`子句。

```sql
testdb=# \d tbl_1
     Table "public.tbl_1"
 Column |  Type   | Modifiers 
--------+---------+-----------
 id     | integer | 
 data   | integer | 

testdb=# SELECT * FROM tbl_1 WHERE id < 300 ORDER BY data;
```

图3.10和图3.11展示了本例中计划器的处理过程。

**图3.10 如何得到例1中代价最小的路径**

![](img/fig-3-10.png)

1. 创建一个`RelOptInfo`结构，将其保存在`PlannerInfo`结构的`simple_rel_array`字段中。

2. 在`RelOptInfo`结构的`baserestrictinfo`字段中，添加一条`WHERE`子句。

   `WHERE`子句`id<300`会经由[`initsplan.c`](https://github.com/postgres/postgres/blob/master/src/backend/optimizer/plan/initsplan.c)中定义的`distribute_restrictinfo_to_rels()`函数，添加至列表变量`baserestrictinfo`中。另外由于目标表上没有相关索引，`RelOptInfo`的`indexlist`字段为空。

3. 为了满足排序要求，[`planner.c`](https://github.com/postgres/postgres/blob/master/src/backend/optimizer/plan/planner.c)中的`standard_qp_callback()`函数会在`PlannerInfo`的`sor_pathkeys`字段中添加一个`pathkey`。

   `Pathkey`是表示路径排序顺序的数据结构。本例因为查询包含一条`ORDER BY`子句，且该子句中的列为`data`，故`data`会被包装为`pathkey`，放入列表变量`sort_pathkeys`中。

4. 创建一个`Path`结构，并使用`cost_seqscan`函数估计顺序扫描的代价，并将代价写入`Path`中。然后使用[`pathnode.c`](https://github.com/postgres/postgres/blob/master/src/backend/optimizer/util/pathnode.c)中定义的`add_path()`函数，将该路径添加至`RelOptInfo`中。

   如之前所提到过的，`Path`中同时包含启动代价和总代价，都是由`cost_seqscan`函数所估计的。

在本例中，因为目标表上没有索引，计划器只估计了顺序扫描的代价，因此最小代价是自动确定的。

**图3.11 如何得到例1中代价最小的路径（接图3.10）**

![](img/fig-3-11.png)

5. 创建一个新的`RelOptInfo`结构，用于处理`ORDER BY`子句。

   注意新的`RelOptInfo`没有`baserestrictinfo`字段，该信息已经被`WHERE`子句所持有。

6. 创建一个排序路径，并添加到新的`RelOptInfo`中；然后让`SortPath`的`subpath`字段指向顺序扫描的路径。

    ```c
    typedef struct SortPath
    {
        Path	path;
        Path	*subpath;		/* 代表输入来源的子路径 */
    } SortPath;
    ```
    `SortPath`结构包含两个`Path`结构：`path`与`subpath`；`path`中存储了排序算子本身的相关信息，而`subpath`则指向之前得到的代价最小的路径。

    注意顺序扫描路径中`parent`字段，该字段指向之前的`RelOptInfo`结构体（也就是在`baserestrictinfo`中存储着`WHERE`子句的那个RelOptInfo）。因此在下一步创建计划树的过程中，尽管新的`RelOptInfo`结构并未包含`baserestrictinfo`，但计划器可以创建一个包含`Filter`的顺序扫描节点，将`WHERE`子句作为过滤条件。

这里已经获得了代价最小的访问路径，然后就可以基于此生成一颗计划树。3.3.3节描述了相关的细节。

#### 3.3.2.2 例2

下面我们将研究另一个单表查询的例子，这一次表上有两个索引，而查询带有一个`WHERE`子句。

```sql
testdb=# \d tbl_2
     Table "public.tbl_2"
 Column |  Type   | Modifiers 
--------+---------+-----------
 id     | integer | not null
 data   | integer | 
Indexes:
    "tbl_2_pkey" PRIMARY KEY, btree (id)
    "tbl_2_data_idx" btree (data)

testdb=# SELECT * FROM tbl_2 WHERE id < 240;
```

图3.12到3.14展示了本例中计划器的处理过程。

1. 创建一个`RelOptInfo`结构体

2. 在`baserestrictinfo`中添加一个`WHERE`子句；并将目标表上的索引（们）添加到`indexlist`中。

   在本例中，`WHERE`子句`'id <240'`会被添加至`baserestrictinfo`中，而两个索引：`tbl_2_pkey`和`tbl_2_data_idx`会被添加至`RelOptInfo`的列表变量`indexlist`中。

3. 创建一条路径，估计其顺序扫描代价，并添加到`RelOptInfo`的`pathlist`中。

**图3.12 如何得到例2中代价最小的路径**

![](img/fig-3-12.png)

```c
typedef struct IndexPath
{
	Path		path;
	IndexOptInfo 	*indexinfo;
	List	   	*indexclauses;
	List	   	*indexquals;
	List	   	*indexqualcols;
	List	   	*indexorderbys;
	List	   	*indexorderbycols;
	ScanDirection 	indexscandir;
	Cost		indextotalcost;
	Selectivity 	indexselectivity;
} IndexPath;

/*
 * IndexOptInfo
 *		Per-index information for planning/optimization
 *
 *		indexkeys[], indexcollations[], opfamily[], and opcintype[]
 *		each have ncolumns entries.
 *
 *		sortopfamily[], reverse_sort[], and nulls_first[] likewise have
 *		ncolumns entries, if the index is ordered; but if it is unordered,
 *		those pointers are NULL.
 *
 *		Zeroes in the indexkeys[] array indicate index columns that are
 *		expressions; there is one element in indexprs for each such column.
 *
 *		For an ordered index, reverse_sort[] and nulls_first[] describe the
 *		sort ordering of a forward indexscan; we can also consider a backward
 *		indexscan, which will generate the reverse ordering.
 *
 *		The indexprs and indpred expressions have been run through
 *		prepqual.c and eval_const_expressions() for ease of matching to
 *		WHERE clauses. indpred is in implicit-AND form.
 *
 *		indextlist is a TargetEntry list representing the index columns.
 *		It provides an equivalent base-relation Var for each simple column,
 *		and links to the matching indexprs element for each expression column.
 *
 *		While most of these fields are filled when the IndexOptInfo is created
 *		(by plancat.c), indrestrictinfo and predOK are set later, in
 *		check_index_predicates().
 */
typedef struct IndexOptInfo
{
	NodeTag		type;
	Oid		indexoid;		/* OID of the index relation */
	Oid		reltablespace;		/* tablespace of index (not table) */
	RelOptInfo 	*rel;			/* back-link to index's table */

	/* index-size statistics (from pg_class and elsewhere) */
	BlockNumber     pages;			/* number of disk pages in index */
	double		tuples;			/* number of index tuples in index */
	int		tree_height;		/* index tree height, or -1 if unknown */

	/* index descriptor information */
	int		ncolumns;		/* number of columns in index */
	int		*indexkeys;		/* column numbers of index's keys, or 0 */
	Oid		*indexcollations;	/* OIDs of collations of index columns */
	Oid		*opfamily;		/* OIDs of operator families for columns */
	Oid		*opcintype;		/* OIDs of opclass declared input data types */
	Oid		*sortopfamily;		/* OIDs of btree opfamilies, if orderable */
	bool	   	*reverse_sort;		/* is sort order descending? */
	bool	   	*nulls_first;		/* do NULLs come first in the sort order? */
	bool	   	*canreturn;		/* which index cols can be returned in an index-only scan? */
	Oid		relam;			/* OID of the access method (in pg_am) */

	List	   	*indexprs;		/* expressions for non-simple index columns */
	List	   	*indpred;		/* predicate if a partial index, else NIL */

	List	   	*indextlist;		/* targetlist representing index columns */

	List	   	*indrestrictinfo;	/* parent relation's baserestrictinfo list,
						 * less any conditions implied by the index's
						 * predicate (unless it's a target rel, see
						 * comments in check_index_predicates()) */

	bool		predOK;			/* true if index predicate matches query */
	bool		unique;			/* true if a unique index */
	bool		immediate;		/* is uniqueness enforced immediately? */
	bool		hypothetical;		/* true if index doesn't really exist */

	/* Remaining fields are copied from the index AM's API struct: */
	bool		amcanorderbyop;     	/* does AM support order by operator result? */
	bool		amoptionalkey;		/* can query omit key for the first column? */
	bool		amsearcharray;		/* can AM handle ScalarArrayOpExpr quals? */
	bool		amsearchnulls;		/* can AM search for NULL/NOT NULL entries? */
	bool		amhasgettuple;		/* does AM have amgettuple interface? */
	bool		amhasgetbitmap; 	/* does AM have amgetbitmap interface? */
	/* Rather than include amapi.h here, we declare amcostestimate like this */
	void		(*amcostestimate) ();	/* AM's cost estimator */
} IndexOptInfo;
```

4. 创建一个`IndexPath`，估计索引扫描的代价，并通过`add_path()`函数将`IndexPath`添加到`RelOptInfo`的`pathlist`中。

   在本例中有两个索引：`tbl_2_pkey`与`tbl_2_data_index`，这些索引会按先后顺序依次处理。

   一条针对`tbl_2_pkey`的`IndexPath`会先被创建出来，并进行启动代价与总代价的评估。在本例中，`tbl_2_pkey`是`id`列上的索引，而`WHERE`子句也包含该`id`列；因此`WHERE`子句会被存储在`IndexPath`的`indexclauses`字段中。

5. 创建另一个`IndexPath`，估计另一种索引扫描的代价，并将该`IndexPath`添加到`RelOptInfo`的`pathlist`中。

   接下来，与`tbl_2_data_idx`相应的`IndexPath`会被创建出来，并进行代价估计。本例中`tbl_2_data_idx`并没有相关的`WHERE`子句；因此其`indexclauses`为空。

> 注意`add_path()`函数并不总是真的会将路径添加到路径列表中。这一操作相当复杂，故这里就省去了具体描述。详细细节可以参考`add_path()`函数的注释。

**图3.13 如何得到例2中代价最小的路径（接图3.12）**

![](img/fig-3-13.png)

6. 创建一个新的`RelOptInfo`结构

7. 将代价最小的路径，添加到新`RelOptInfo`的`pathlist`中。

   本例中代价最小的路径是使用`tbl_2_pkey`的索引路径；故将该路径添加到新的`RelOptInfo`中。

**图3.14 如何得到例2中代价最小的路径（接图3.13）**

![](img/fig-3-14.png)

### 3.3.3 创建计划树

在最后一步中，计划器按照代价最小的路径生成一颗计划树。 

计划树的根节点是定义在[`plannodes.h`](https://github.com/postgres/postgres/blob/master/src/include/nodes/plannodes.h)中的`PlannedStmt`结构，包含19个字段，其中有4个代表性字段：

+ **`commandType`**存储操作的类型，诸如`SELECT`，`UPDATE`和`INSERT`。
+ **`rtable`**存储范围表的列表（`RangeTblEntry`的列表）。
+ **`relationOids`**存储与查询相关表的`oid`。
+ **`plantree`**存储着一颗由计划节点组成的计划树，每个计划节点对应着一种特定操作，诸如顺序扫描，排序和索引扫描。

```c
/* ----------------
 *		PlannedStmt 节点
 * 计划器的输出是一颗计划树，PlannedStmt是计划树的根节点。
 * PlannedStmt存储着执行器所需的“一次性”信息。
 * ----------------*/
typedef struct PlannedStmt
{
	NodeTag		type;
	CmdType		commandType;		/* 增|删|改|查 */
	uint32		queryId;			/* 查询标识符 (复制自Query) */
	bool		hasReturning;		/* 增|删|改是否带有RETURNING? */
	bool		hasModifyingCTE;	/* WITH子句中是否出现了增|删|改？ */
	bool		canSetTag;			/* 我是否设置了命令结果标记？ */
	bool		transientPlan;		/* 当TransactionXmin变化时重新进行计划? */
	bool		dependsOnRole;		/* 执行计划是否特定于当前的角色？ */
	bool		parallelModeNeeded;	/* 需要并行模式才能执行？ */
	struct Plan *planTree;			/* 计划节点树 */
	List	   	*rtable;			/* RangeTblEntry节点的列表 */
	
    /* 目标关系上用于增|删|改的范围表索引 */
	List	   	*resultRelations;   /* RT索引的整数列表, 或NIL */
	Node	   	*utilityStmt;		/* 如为DECLARE CURSOR则非空 */
	List	   	*subplans;			/* SubPlan表达式的计划树 expressions */
	Bitmapset  	*rewindPlanIDs;		/* indices of subplans that require REWIND */
	List	   	*rowMarks;			/* PlanRowMark列表 */
	List	   	*relationOids;		/* 计划所依赖的关系OID列表 */
	List	   	*invalItems;		/* 其他依赖，诸如PlanInvalItems */
	int			nParamExec;			/* 使用的PARAM_EXEC参数数量 */
} PlannedStmt;
```

 如上所述，计划树包含各式各样的计划节点。`PlanNode`是所有计划节点的基类，其他计划节点都会包含`PlanNode`结构。比如顺序扫描节点`SeqScanNode`，包含一个`PlanNode`和一个整型变量`scanrelid`。`PlanNode`包含14个字段。下面是7个代表性字段：

+ `startup_cost`和`total_cost`是该节点对应操作的预估代价。
+ `rows`是计划器预计扫描的行数。
+ `targetlist`保存了该查询树中目标项的列表。
+ `qual`储存了限定条件的列表。
+ `lefttree`和`righttree`用于添加子节点。

```c
/* ----------------
 * 计划节点(Plan Node)
 *
 * 所有的计划节点都"派生"自Plan结构，将其作为自己的第一个字段。这样确保了当其强制转换为Plan
 * 结构时所有东西都能正常工作。(当作为通用参数传入执行器时，节点指针会很频繁地转换为Plan*)
 *
 * 我们从来不会真的去实例化任何Plan节点，它只是所有Plan类型节点的公共抽象父类。
 * ----------------
 */
typedef struct Plan
{
	NodeTag		type;
	/* 计划的预估执行开销 ( 详情见 costsize.c )	 */
	Cost		startup_cost;	/* 获取第一条元组前的代价 */
	Cost		total_cost;		/* 获取所有元组的代价 */

	/* 计划器对该计划步骤返回结果大小的估计 */
	double		plan_rows;		/* 计划预期产出的行数 */
	int			plan_width;		/* 以字节计的行宽 */

	/* 并行查询所需的信息 */
	bool		parallel_aware; /* 是否涉及到并行逻辑？ */

	/* 所有计划类型的公有结构化数据 */
	int			plan_node_id;	/* 在整个计划树范围内唯一的标识 */
	List	   	*targetlist;	/* 该节点需要计算的目标列表 */
	List	   	*qual;			/* 隐式合取化处理的 限制条件 列表 */
	struct Plan *lefttree;		/* 输入的查询树 */
	struct Plan *righttree;
	List	   	*initPlan;	/* Init Plan 节点 (无关子查询表达式) */
	/*
	 * Information for management of parameter-change-driven rescanning
	 *
	 * extParam includes the paramIDs of all external PARAM_EXEC params
	 * affecting this plan node or its children.  setParam params from the
	 * node's initPlans are not included, but their extParams are.
	 *
	 * allParam includes all the extParam paramIDs, plus the IDs of local
	 * params that affect the node (i.e., the setParams of its initplans).
	 * These are _all_ the PARAM_EXEC params that affect this node.
	 */
	Bitmapset	*extParam;
	Bitmapset  	*allParam;
} Plan;

/* ------------
 * 扫描节点(Scan nodes)
 * ----------- */
typedef unsigned int Index;

typedef struct Scan
{
	Plan		plan;
	Index		scanrelid;		/* relid 是访问范围表的索引 */
} Scan;

/* ----------------
 *	顺序扫描节点
 * ---------------- */
typedef Scan SeqScan;
```

下面是两颗计划树，分别与前一小节中的两个例子对应。

#### 3.3.3.1. 例1

第一个例子是3.3.2.1节例1对应的计划树。图3.11所示的代价最小的路径，是由一个排序路径和一个顺序扫描路径组合而成。根路径是排序路径，而其子路径为顺序扫描路径。尽管这里忽略了大量细节，但是从代价最小的路径中生成计划树的过程是显而易见的。在本例中，一个 `SortNode`被添加到`PlannedStmt`结构中，而`SortNode`的左子树上则挂载了一个`SeqScanNode`，如图3.15(a)所示。

在`SortNode`中，左子树`lefttree`指向`SeqScanNode`。

在`SeqScanNode`中，`qual`保存了`WHERE`子句：`'id<300'`。

```c
typedef struct Sort
{
	Plan		plan;
	int			numCols;			/* 排序键 列的数目 */
	AttrNumber 	*sortColIdx;		/* 它们在目标列表中的位置序号 */
	Oid			*sortOperators;		/* 排序所赖运算符的OID  */
	Oid			*collations;		/* collation的OID  */
	bool	   	*nullsFirst;		/* NULLS FIRST/LAST 方向 */
} Sort;
```

**图3.15. 计划树的例子**

![](img/fig-3-15.png)



#### 3.3.3.2 例2

第二个例子是3.3.2.2节例2对应的计划树。其代价最小的路径为索引扫描路径，如图3.14所示。因此计划树由单个`IndexScanNode`独立组成，如图3.15(b)所示。

在本例中，`WHERE`子句`id < 240`是一个访问谓词，它储存在`IndexScanNode`的`indexqual`字段中。

```c
/* ----------------
 *		索引扫描节点
 *
 * indexqualorig is an implicitly-ANDed list of index qual expressions, each
 * in the same form it appeared in the query WHERE condition.  Each should
 * be of the form (indexkey OP comparisonval) or (comparisonval OP indexkey).
 * The indexkey is a Var or expression referencing column(s) of the index's
 * base table.  The comparisonval might be any expression, but it won't use
 * any columns of the base table.  The expressions are ordered by index
 * column position (but items referencing the same index column can appear
 * in any order).  indexqualorig is used at runtime only if we have to recheck
 * a lossy indexqual.
 *
 * indexqual has the same form, but the expressions have been commuted if
 * necessary to put the indexkeys on the left, and the indexkeys are replaced
 * by Var nodes identifying the index columns (their varno is INDEX_VAR and
 * their varattno is the index column number).
 *
 * indexorderbyorig is similarly the original form of any ORDER BY expressions
 * that are being implemented by the index, while indexorderby is modified to
 * have index column Vars on the left-hand side.  Here, multiple expressions
 * must appear in exactly the ORDER BY order, and this is not necessarily the
 * index column order.  Only the expressions are provided, not the auxiliary
 * sort-order information from the ORDER BY SortGroupClauses; it's assumed
 * that the sort ordering is fully determinable from the top-level operators.
 * indexorderbyorig is used at runtime to recheck the ordering, if the index
 * cannot calculate an accurate ordering.  It is also needed for EXPLAIN.
 *
 * indexorderbyops is a list of the OIDs of the operators used to sort the
 * ORDER BY expressions.  This is used together with indexorderbyorig to
 * recheck ordering at run time.  (Note that indexorderby, indexorderbyorig,
 * and indexorderbyops are used for amcanorderbyop cases, not amcanorder.)
 *
 * indexorderdir specifies the scan ordering, for indexscans on amcanorder
 * indexes (for other indexes it should be "don't care").
 * ----------------
 */
typedef struct Scan
{
        Plan        plan;
        Index       scanrelid;          /* relid is index into the range table */
} Scan;

typedef struct IndexScan
{
	Scan	   scan;
	Oid	   indexid;		/* OID of index to scan */
	List	   *indexqual;		/* list of index quals (usually OpExprs) */
	List	   *indexqualorig;	/* the same in original form */
	List	   *indexorderby;	/* list of index ORDER BY exprs */
	List	   *indexorderbyorig;	/* the same in original form */
	List	   *indexorderbyops;	/* OIDs of sort ops for ORDER BY exprs */
	ScanDirection indexorderdir;	/* forward or backward or don't care */
} IndexScan;
```






## 3.4 执行器如何工作

在单表查询中，执行器从下至上执行计划节点，并调用相应节点的处理函数。

每个计划节点有执行相应操作的函数，这些函数在src/backend/executor目录中。比如，执行顺序扫描的的函数（SeqScan）在[nodeSeqscan.c](https://github.com/postgres/postgres/blob/master/src/backend/executor/nodeIndexscan.c)中；执行索引扫描的函数（IndexScanNode）定义在[nodeIndexScan.c](https://github.com/postgres/postgres/blob/master/src/backend/executor/nodeIndexscan.c)中；SortNode节点的排序函数定义在[nodeSort.c](https://github.com/postgres/postgres/blob/master/src/backend/executor/nodeSort.c)中等等。

当然，理解执行器的最好方式就是阅读EXPLAIN命令的输出，因为PostgreSQL的EXPLAIN命令几乎就表示一棵计划树。下文基于3.3.3节的例1对此作出解释。

```c
testdb=# EXPLAIN SELECT * FROM tbl_1 WHERE id < 300 ORDER BY data;
                          QUERY PLAN                           
---------------------------------------------------------------
 Sort  (cost=182.34..183.09 rows=300 width=8)
   Sort Key: data
   ->  Seq Scan on tbl_1  (cost=0.00..170.00 rows=300 width=8)
         Filter: (id < 300)
(4 rows)
```

我们从下往上查看EXPLAIN的结果，探究执行器是如何执行的：

**第六行**: 首先，执行器执行[nodeSeqscan.c](https://github.com/postgres/postgres/blob/master/src/backend/executor/nodeSeqscan.c)中定义的顺序扫描操作。

**第四行**：接下来，执行器使用[nodeSort.c](https://github.com/postgres/postgres/blob/master/src/backend/executor/nodeSort.c)中定义的函数，对顺序扫描的结果进行排序。

> ### 临时文件
>
> 执行器使用内存中分配的`work_mem`和`temp_buffers`，但是如果某查询的处理中内存不够，就会使用临时文件。
>
> 使用`Analyze`选项，`EXPLAIN`会真正执行这个查询并展示实际的行数，实际执行时间和实际内存使用。如下例所示：
>
> ```c
> testdb=# EXPLAIN ANALYZE SELECT id, data FROM tbl_25m ORDER BY id;
>                                                         QUERY PLAN                                                        
> --------------------------------------------------------------------------------------------------------------------------
>  Sort  (cost=3944070.01..3945895.01 rows=730000 width=4104) (actual time=885.648..1033.746 rows=730000 loops=1)
>    Sort Key: id
>    Sort Method: external sort  Disk: 10000kB
>    ->  Seq Scan on tbl_25m  (cost=0.00..10531.00 rows=730000 width=4104) (actual time=0.024..102.548 rows=730000 loops=1)
>  Planning time: 1.548 ms
>  Execution time: 1109.571 ms
> (6 rows)
> ```
>
> 在第6行，EXPLAIN命令显示了执行器使用了10000KB的临时文件。
>
> 临时文件临时创建在base/pg_tmp子目录中，遵循如下命名规则
>
> ```bash
> {"pgsql_tmp"} + {创建本文件的Postgres进程PID} . {从0开始的序列号}
> ```
>
> 比如，临时文件`pgsql_tmp8903.5`是pid为`8903`的postgres进程创建的第6个临时文件



## 3.5 连接

​	PostgreSQL 中支持三种**连接（JOIN）**算法：：**嵌套循环连接（Nested Loop Join）**，**归并连接（Merge Join）** ，**散列连接（Hash Join）**。在PostgreSQL中有一些变体。

​	下面，我们假设读者对这三个算法的基本行为有了解。如果你对这些概念不熟悉，请参阅[[1](https://www.amazon.com/dp/0073523321), [2](https://www.amazon.com/dp/0321523067)]。但由于没有太多关于支持数据倾斜的hybrid hash join介绍，这里对该算法细节详细描述。

​	注意，这三种算法支持PostgreSQL中所有的连接操作，不仅仅是`INNER JOIN`，也有`LEFT/RIGHT OUTER JOIN`，`FULL OUTER JOIN`等；但是为了简化描述，本章只关注`NATURAL INNER JOIN`。

### 3.5.1 嵌套循环连接（Nested Loop Join）

**嵌套循环连接**是最基础的连接，可以在任何条件下使用。PostgreSQL中支持原生的还有5中变种。

#### 3.5.1.1 嵌套循环连接

嵌套循环连接无需任何启动代价，因此：
$$
\verb|start-up_cost| = 0
$$
运行时代价与内外表大小的乘积成比例；即，runcost是$O(N_{\verb|outer|}× N_{\verb|inner|})$，这里$N_{\verb|outer|}$和$N_{\verb|inner|}$分别是外表和内表的元组数。$\verb|run_cost|$更准确的定义如下：
$$
\begin{equation}
\verb|run_cost|=(\verb|cpu_operator_cost|+ \verb|cpu_tuple_cost|)× N_{\verb|outer|}× N_{\verb|inner|} + C_{\verb|inner|}× N_{\verb|outer|}+C_{\verb|outer|}
\end{equation}
$$
这里$C_{\verb|outer|}$和$C_{\verb|inner|}$分别是内表和外表的顺序扫描的代价；

**图3.16 嵌套循环连接**

![](img/fig-3-16.png)

总是会估计嵌套循环连接的代价，但是因为经常用到下面描述的高效变体，所以这个join操作符很少用到。

#### 3.5.1.2 物化嵌套循环连接

​	在上面描述的嵌套循环连接中，对于外表的每个元组，都需要扫描内表的所有元组。由于每个外表的元组都需要扫描整个内表，这个处理代价太高，PostgreSQL支持**物化嵌套循环连接（materialized nested loop join）** ，可以减少完整扫描内表的代价。

在运行嵌套循环连接之前，执行使用*temporary tuple storage*组件进行一次扫描，将内表的元组加载到work_mem或者临时文件中。临时元组存储比缓冲区管理器处理内部表元组更加高效，特别是当所有的元组都能装载到`work_mem`中时。

图 3.17展示了物化嵌套循环连接的处理过程。扫描物化元组在内部称为**rescan**。

**图3.17 物化嵌套循环连接**

![](img/fig-3-17.png)

> ##### 临时元组存储
>
> PostgreSQL内部提供了临时元组存储的模块，可以用在物化表，创建混合散列连接的batch等处理中。这个模块的函数在tuplestore.c中，这些函数从work_mem或者临时文件中，读取或写入一串元组。是否用到work_mem或者临时文件取决于出处的元组大小。

基于以下的例子，我们探究一下执行器如何处理计划树中的materialized nested loop join，以及如何估计这个操作的代价。

```sql
testdb=# EXPLAIN SELECT * FROM tbl_a AS a, tbl_b AS b WHERE a.id = b.id;
                              QUERY PLAN                               
-----------------------------------------------------------------------
 Nested Loop  (cost=0.00..750230.50 rows=5000 width=16)
   Join Filter: (a.id = b.id)
   ->  Seq Scan on tbl_a a  (cost=0.00..145.00 rows=10000 width=8)
   ->  Materialize  (cost=0.00..98.00 rows=5000 width=8)
         ->  Seq Scan on tbl_b b  (cost=0.00..73.00 rows=5000 width=8)
(5 rows)
```

首先，展示了执行器的操作符。执行器对上描述的计划树的处理如下：

Line 7：执行器通过顺序扫描将内部表tbl_b进行物化

Line 4：执行器执行了嵌套循环连接操作；外表是`tbl_a`，内表是物化的`tbl_b`

下面，估计‘Materialize’（Line 7）和‘Nested Loop’（Line 4）的代价。假设物化的内部表元组都在work_mem中。

**Materialize**:

没有启动代价；因此，
$$
\begin{equation}
      \verb|start-up_cost| = 0
\end{equation}
$$
运行代价如下定义：
$$
\verb|run_cost| = 2 × \verb|cpu_operator_cost| × N_{\verb|inner|};
$$
得
$$
\verb|run_cost|=2× 0.0025× 5000=25.0
$$
另外，
$$
\verb|total_cost| = (\verb|start-up_cost|+ \verb|total_cost_of_seq_scan|)+ \verb|run_cost|
$$
因此，
$$
\verb|total_cost| = (0.0+73.0)+25.0=98.0
$$
**(Materialized) Nested Loop**:

没有启动代价；因此
$$
\verb|start-up_cost|=0
$$
在估计运行代价之前，先考虑一下rescan的代价。代价定义为如下公式：
$$
\verb|rescan_cost| = \verb|cpu_operator_cost| × N_{\verb|inner|}
$$
这个例子中，
$$
\verb|rescan_cost| = (0.0025)× 5000=12.5
$$
运行时代价定义如下公式
$$
\verb|run_cost| =(\verb|cpu_operator_cost| + \verb|cpu_tuple_cost|)× N_{\verb|inner|}× N_{\verb|outer|} \\
+ \verb|recan_cost|'× (N_{\verb|outer|}-1) + C^{\verb|total|}_{\verb|outer|,\verb|seqscan|} + C^{\verb|total|}_{\verb|materialize|}，
$$
这里 $C^{\verb|total|}_{\verb|outer|,\verb|seqscan|}$代表外部表的全部扫描代价，$C^{\verb|total|}_{\verb|materialize|}$代表物化代价；因此
$$
\verb|run_cost| = ( 0.0025 + 0.01 ) × 5000 × 10000 + 12.5 ×(10000−1)+145.0+98.0=750230.5
$$

#### 3.5.1.3. 使用索引的嵌套循环连接

如果内表上有索引并且index能够找到满足外表每个元组join条件的元组，计划器考虑直接使用这个索引来直接查询内部表元组而不用顺序扫描。这个变体叫做**索引嵌套循环连接（indexed nested loop join）**，如图3.18。尽管叫索引"嵌套循环连接"，这个算法可以只扫描一次外部表；因此，可以高效执行join操作。

**图.3.18 索引嵌套循环连接**

![](img/fig-3-18.png)

一个关于索引嵌套循环连接的例子如下。

```sql
testdb=# EXPLAIN SELECT * FROM tbl_c AS c, tbl_b AS b WHERE c.id = b.id;
                                   QUERY PLAN                                   
--------------------------------------------------------------------------------
 Nested Loop  (cost=0.29..1935.50 rows=5000 width=16)
   ->  Seq Scan on tbl_b b (cost=0.00..73.00 rows=5000 width=8)
   ->  Index Scan using tbl_c_pkey on tbl_c c  (cost=0.29..0.36 rows=1 width=8)
         Index Cond: (id = b.id)
(4 rows)
```

​	在第6行中，展示了访问内表中元组的代价。即，查找内表中满足第七行条件的。

​	在第7行的索引条件（`id=b.id`）中，‘b.id’是连接条件中的外表属性的值。当外表按照顺序扫描取到一个元组是，就按照第6行的索引扫描路径，查找内表中的连接元组。换句话说，将外表元组的值传入索引扫描中，内表按照索引扫描找到满足join条件的元组。这种索引路径称为**参数化（索引）路径（parameterized (index) path）**，细节见PostgreSQ源码optimizer/README。

这个嵌套循环连接的启动代价等于第6行索引扫描的代价；因此有
$$
\verb|start-up_cost| = 0.285
$$
索引嵌套循环扫描的整体代价定义如下等式：
$$
\verb|total_cost|=\verb|cpu_tuple_cost| + C^{\verb|total|}_{\verb|inner,parameterized|}× N_{\verb|outer|}+C^{\verb|run|}_{\verb|outer,seqscan|}
$$
这里$C^{\verb|total|}_{\verb|inner,parameterized|}$是参数化内表索引扫描的整体代价，

这里，
$$
\verb|total_cost|=(0.01+0.3625)× 5000 + 73.0 = 1935.5
$$
并且运行代价等于
$$
\verb|run_cost| = 1935.5-0.285=1935.215
$$
如上所示，索引嵌套扫描的整体低价是$O(N_{\verb|outer|})$。

#### 3.5.1.4 其他变种

如果外表有一个和连接条件相关的索引，外表同样可以利用索引扫描，而不是顺序扫描。特别地，如果索引相关的属性能够用在`WHERE`子句中，作为访问谓词，外表的查找范围会缩小；因此，嵌套循环连接的代价会显著减少。

PostgreSQL执行是三种嵌套循环连接的变体，如图. 3.19。

**图3.19.  外部表带索引扫描的嵌套循环连接的三个变体**

![out](img/fig-3-19.png)

这些连接的EXPLAIN命令结果如下。

1. 带有外表索引扫描的嵌套循环连接

   ```sql
   testdb=# SET enable_hashjoin TO off;
   SET
   testdb=# SET enable_mergejoin TO off;
   SET
   testdb=# EXPLAIN SELECT * FROM tbl_c AS c, tbl_b AS b WHERE c.id = b.id AND c.id = 500;
                                      QUERY PLAN                                   
   --------------------------------------------------------------------------------
    Nested Loop  (cost=0.29..93.81 rows=1 width=16)
      ->  Index Scan using tbl_c_pkey on tbl_c c  (cost=0.29..8.30 rows=1 width=8)
            Index Cond: (id = 500)
      ->  Seq Scan on tbl_b b  (cost=0.00..85.50 rows=1 width=8)
            Filter: (id = 500)
   (5 rows)
   ```

2. 带有外表索引扫描的物化嵌套循环连接

   ```sql
   testdb=# SET enable_hashjoin TO off;
   SET
   testdb=# SET enable_mergejoin TO off;
   SET
   testdb=# EXPLAIN SELECT * FROM tbl_c AS c, tbl_b AS b WHERE c.id = b.id AND c.id < 40 AND b.id < 10;
                                      QUERY PLAN                                    
   ---------------------------------------------------------------------------------
    Nested Loop  (cost=0.29..99.76 rows=1 width=16)
      Join Filter: (c.id = b.id)
      ->  Index Scan using tbl_c_pkey on tbl_c c  (cost=0.29..8.97 rows=39 width=8)
            Index Cond: (id < 40)
      ->  Materialize  (cost=0.00..85.55 rows=9 width=8)
            ->  Seq Scan on tbl_b b  (cost=0.00..85.50 rows=9 width=8)
                  Filter: (id < 10)
   (7 rows)
   ```

3. 带有外表索引扫描的索引嵌套循环连接

   ```sql
   testdb=# SET enable_hashjoin TO off;
   SET
   testdb=# SET enable_mergejoin TO off;
   SET
   testdb=# EXPLAIN SELECT * FROM tbl_a AS a, tbl_d AS d WHERE a.id = d.id AND a.id <  40;
                                      QUERY PLAN                                    
   ---------------------------------------------------------------------------------
    Nested Loop  (cost=0.57..173.06 rows=20 width=16)
      ->  Index Scan using tbl_a_pkey on tbl_a a  (cost=0.29..8.97 rows=39 width=8)
            Index Cond: (id < 40)
      ->  Index Scan using tbl_d_pkey on tbl_d d  (cost=0.28..4.20 rows=1 width=8)
            Index Cond: (id = a.id)
   (5 rows)
   ```

### 3.5.2 归并连接（Merge Join）

与嵌套循环连接不同，归并连接只能用于自然连接和等值连接。

合并连接的开销由函数`initial_cost_mergejoin()`和`final_cost_mergejoin()`估算。

由于确切的成本估算很复杂，因此省略它并且仅显示合并连接算法的运行时顺序。 合并连接的启动成本是内表和外表的排序成本之和; 因此，启动成本是
$$
O(N_{\verb|outer|} \log_2(N_{\verb|outer|}) + N_{\verb|inner|} \log_2(N_{\verb|inner|}))
$$
这里$N_{outer}$和$N_{inner}$是分别是外表和内表的元组数，运行代价是$O(N_{\verb|outer|}+N_{\verb|inner|})$。

和嵌套循环连接类似，merge join在PostgreSQL中有4个变体。

#### 3.5.2.1 归并连接

图3.20显示了归并连接的概念图。

**图3.20. 合并连接**

![](img/fig-3-20.png)

如果所有元组都可以存储在内存中，那么排序操作将能够在内存中进行; 否则，使用临时文件。

EXPLAIN命令的归并连接结果的具体示例如下所示。

```sql
testdb=# EXPLAIN SELECT * FROM tbl_a AS a, tbl_b AS b WHERE a.id = b.id AND b.id < 1000;
                               QUERY PLAN
-------------------------------------------------------------------------
 Merge Join  (cost=944.71..984.71 rows=1000 width=16)
   Merge Cond: (a.id = b.id)
   ->  Sort  (cost=809.39..834.39 rows=10000 width=8)
         Sort Key: a.id
         ->  Seq Scan on tbl_a a  (cost=0.00..145.00 rows=10000 width=8)
   ->  Sort  (cost=135.33..137.83 rows=1000 width=8)
         Sort Key: b.id
         ->  Seq Scan on tbl_b b  (cost=0.00..85.50 rows=1000 width=8)
               Filter: (id < 1000)
(9 rows)
```

第9行：执行器使用顺序扫描（第11行）对内部表tbl_b进行排序。
第6行：执行器使用顺序扫描（第8行）对外表tbl_a进行排序。
第4行：执行器执行归并连接操作; 外部表是已排序的tbl_a，内部表是已排序的tbl_b。

#### 3.5.2.2 物化归并连接

与嵌套循环连接中的相同，归并连接还支持**物化归并连接（Materialized Merge Join）**，将内部表物化，以使内部表扫描更有效。

**图3.21 物化合并连接**

![](img/fig-3-21.png)

图中显示了物化归并连接的结果的示例。 很容易发现上面的归并连接结果的差异是第9行：'Materialise'。

```
testdb=# EXPLAIN SELECT * FROM tbl_a AS a, tbl_b AS b WHERE a.id = b.id;
                                    QUERY PLAN                                     
-----------------------------------------------------------------------------------
 Merge Join  (cost=10466.08..10578.58 rows=5000 width=2064)
   Merge Cond: (a.id = b.id)
   ->  Sort  (cost=6708.39..6733.39 rows=10000 width=1032)
         Sort Key: a.id
         ->  Seq Scan on tbl_a a  (cost=0.00..1529.00 rows=10000 width=1032)
   ->  Materialize  (cost=3757.69..3782.69 rows=5000 width=1032)
         ->  Sort  (cost=3757.69..3770.19 rows=5000 width=1032)
               Sort Key: b.id
               ->  Seq Scan on tbl_b b  (cost=0.00..1193.00 rows=5000 width=1032)
(9 rows)
```

第10行：执行器使用顺序扫描（第12行）对内部表*tbl_b*进行排序。
第9行：执行器实现了排序的*tbl_b*的结果。
第6行：执行器使用顺序扫描（第8行）对外表*tbl_a*进行排序。
第4行：执行器执行归并连接操作; 外部表是已排序的*tbl_a*，而内部表是已实现的已排序的*tbl_b*。

#### 3.5.2.3 其他变体

与嵌套循环连接类似，PostgreSQL中的归并连接也有带有外部表的索引扫描的变体。

**图3.22 合并的三个变体与外部索引扫描连接**

![](img/fig-3-22.png)

这些连接的`EXPLAIN`结果显示如下。

1. 合并外部索引扫描的连接

   ```
   testdb=# SET enable_hashjoin TO off;
   SET
   testdb=# SET enable_nestloop TO off;
   SET
   testdb=# EXPLAIN SELECT * FROM tbl_c AS c, tbl_b AS b WHERE c.id = b.id AND b.id < 1000;
                                         QUERY PLAN                                      
   --------------------------------------------------------------------------------------
    Merge Join  (cost=135.61..322.11 rows=1000 width=16)
      Merge Cond: (c.id = b.id)
      ->  Index Scan using tbl_c_pkey on tbl_c c  (cost=0.29..318.29 rows=10000 width=8)
      ->  Sort  (cost=135.33..137.83 rows=1000 width=8)
            Sort Key: b.id
            ->  Seq Scan on tbl_b b  (cost=0.00..85.50 rows=1000 width=8)
                  Filter: (id < 1000)
   (7 rows)
   ```

2. 物化合并连接与外部索引扫描

   ```
   testdb=# SET enable_hashjoin TO off;
   SET
   testdb=# SET enable_nestloop TO off;
   SET
   testdb=# EXPLAIN SELECT * FROM tbl_c AS c, tbl_b AS b WHERE c.id = b.id AND b.id < 4500;
                                         QUERY PLAN                                      
   --------------------------------------------------------------------------------------
    Merge Join  (cost=421.84..672.09 rows=4500 width=16)
      Merge Cond: (c.id = b.id)
      ->  Index Scan using tbl_c_pkey on tbl_c c  (cost=0.29..318.29 rows=10000 width=8)
      ->  Materialize  (cost=421.55..444.05 rows=4500 width=8)
            ->  Sort  (cost=421.55..432.80 rows=4500 width=8)
                  Sort Key: b.id
                  ->  Seq Scan on tbl_b b  (cost=0.00..85.50 rows=4500 width=8)
                        Filter: (id < 4500)
   (8 rows)
   ```

3. 索引归并连接与外部索引扫描

   ```
   testdb=# SET enable_hashjoin TO off;
   SET
   testdb=# SET enable_nestloop TO off;
   SET
   testdb=# EXPLAIN SELECT * FROM tbl_c AS c, tbl_d AS d WHERE c.id = d.id AND d.id < 1000;
                                         QUERY PLAN                                      
   --------------------------------------------------------------------------------------
    Merge Join  (cost=0.57..226.07 rows=1000 width=16)
      Merge Cond: (c.id = d.id)
      ->  Index Scan using tbl_c_pkey on tbl_c c  (cost=0.29..318.29 rows=10000 width=8)
      ->  Index Scan using tbl_d_pkey on tbl_d d  (cost=0.28..41.78 rows=1000 width=8)
            Index Cond: (id < 1000)
   (5 rows)
   ```

### 3.5.3 散列连接（Hash Join）

​	与归并连接类似，散列连接（Hash Join）只能用于自然连接和等值连接。

​	PostgreSQL中的散列连接的行为有所不同，具体取决于表的大小。 如果目标表足够小（更确切地说，内部表的大小是work_mem的25％或更小），那么它将是一个简单的两阶段的**内存散列连接（in-memory hash join）**; 否则，将会使用带有倾斜方法的**混合散列连接（hybrid hash join）**。

​	在本小节中，描述了PostgreSQL中两个散列连接的执行。

​	省略了对成本估算的讨论，因为它很复杂。如果在搜索和插入哈希表时没有冲突， 粗略地说，启动和运行成本是$O(N_{\verb|outer|} + N_{\verb|inner|})$。

#### 3.5.3.1 内存散列连接

​	在该子部分中，描述了内存中的散列连接。

​	这个内存中的散列连接在工作内存上处理，散列表区域在PostgreSQL中称为**batch**。 **batch**有多个散列槽，内部称为**buckets**，桶的数量由nodeHash.c中定义的`ExecChooseHashTableSize()`函数确定。 桶的数量总是$2^n$，这里n是整数。

​	内存中的散列连接有两个阶段：构建阶段和探测阶段。 在构建阶段，内部表的所有元组都插入到batch中; 在探测阶段，将外表的每个元组与batch中的内元组进行比较，如果满足连接条件，则将其连接起来。

这里示出了一个具体示例，以清楚地理解该操作。 假设使用散列连接执行下面显示的查询。

```sql
SELECT * FROM tbl_outer AS outer, tbl_inner AS inner WHERE inner.attr1 = outer.attr2;
```

在下文中，示出了散列连接的操作。 参见图3.23和3.24。

**图3.23 内存中散列连接中的构建阶段**

![](img/fig-3-23.png)

1. 在work_mem上创建一个batch。

   在这个例子中，batch有八个bucket; 也就是说，桶的数量是2到3次方。

2. 将内表的第一个元组插入batch的相应bucket中。

   详情如下：

   1. 计算连接条件中涉及的第一个元组属性的哈希键。

      在此示例中，使用内置哈希函数计算第一个元组的属性“attr1”的哈希键，因为WHERE子句是“inner.attr1 = outer.attr2”。

   2. 将第一个元组插入相应的bucket中。

      假设第一元组的哈希键是二进制表示法的'0x000 ... 001'; 也就是说，最后三位是'001'。 在这种情况下，将该元组插入到密钥为“001”的桶中。

   在本文档中，构建batch的此类插入操作由此运算符表示：⊕

3. 插入内表的剩余元组。

**图3.24. 内存Hash连接的probe阶段**

![](img/fig-3-24.png)

1. 探测外表的第一个元组。

   详情如下：

   1. 计算第一个元组属性的哈希键，该属性涉及外表的连接条件。

      在这个例子中，假设第一个元组的属性'attr2'的哈希键是'0x000 ... 100';也就是说，最后三位是'100'。

   2. 如果满足连接条件，则将外部表的第一个元组与batch中的内部元组进行比较，并连接元组。

      因为第一个元组的散列键的最后三位是'100'，excutor检索属于其键为'100'的bucket的元组，并比较由连接条件指定的表的各个属性的两个值（由WHERE子句定义）。

      如果满足连接条件，则将连接外表的第一个元组和内表的相应元组;否则，excutor不做任何事情。

      在此示例中，密钥为“100”的bucket具有Tuple_C。如果Tuple_C的attr1等于第一个元组的attr2（Tuple_W），则Tuple_C和Tuple_W将被连接并保存到内存或临时文件中。

      在本文档中，此运算符表示此类操作以探测批处理：⊗

2. 探测外表的剩余元组。

#### 3.5.3.2 带倾斜的混合哈希

当内部表的元组无法存储在work_mem中的一个批处理中时，PostgreSQL使用具有偏移算法的混合散列连接，该算法是基于混合散列连接的变体。

首先，描述混合散列连接的基本概念。在第一个构建和探测阶段，PostgreSQL准备多个批次。batch的数量与bucket的数量相同，由ExecChooseHashTableSize（）函数确定;它总是$ 2 ^ m $，其中m是整数。在此阶段，只在work_mem中分配一个batch，而其他batch则作为临时文件创建;并且属于这些batch的元组，将写入相应的文件并使用临时元组存储功能进行保存。

图3.25说明了如何将元组存储在四个（$ 2 ^ 2 $）batch中。在这种情况下，哪个batch存储每个元组由元组的散列键的最后5位的前两位确定，因为buckets和batchs的大小分别是$ 2 ^ 3 $和$ 2 ^ 2 $。 Batch_0存储散列键的最后5位在'00000'和'00111'之间的元组，Batch_1存储散列键的最后5位在'01000'和'01111'之间的元组，依此类推。

**图. 3.25 多路混合Hash连接**

![](img/fig-3-25.png)

在混合散列连接中，构建和探测阶段的执行次数与batch数相同，因为内部表和外部表存储在相同数量的batch中。在构建和探测阶段的第一轮中，不仅创建了每个批处理，而且还处理了内部表和外部表的第一组batch。另一方面，第二轮和后续轮的处理需要向从临时文件写入和重新加载，因此这些是昂贵的过程。因此，PostgreSQL还准备了一个名为skew的特殊batch，以便在第一轮中更有效地处理许多元组。

这个skew batch存储将与外表元组连接的内表元组，这些外表元组的连接条件中的属性的MCV值相对较大。然而，因为这种解释不容易理解，所以将使用具体示例来解释。

假设有两个表：customers和purchase_history。客户表由两个属性组成：name和address； purchase_history表由两个属性组成：customer_name和buying_item。客户表有10,000行，purchase_history表有1,000,000行。前10％的客户购买了所有商品的70％。

在这些假设下，让我们考虑当执行下面的查询时，带skew batch的混合散列连接如何在第一轮中执行。

```sql
testdb=# SELECT * FROM customers AS c, purchase_history AS h WHERE c.name = h.customer_name;
```

如果客户的表是内部表，并且`purchase_history`是外部表，则使用`purchase_history`表的MCV值将前10％的客户存储在倾斜批次中。 请注意，引用外部表的MCV值是为了将内部表元组插入到skew batch中。 在第一轮的探测阶段，外表（purchase_history）的70％元组将与skew batch中存储的元组连接。 这样，外表分布越不均匀，就可以在第一轮中处理外表的许多元组。

在下文中，示出了带有skew batch的混合散列连接的工作。 参见图3.26至3.29。

**图3.26. 混合散列的构建阶段的第一轮**

![](img/fig-3-26.png)

1. 在work_mem上创建batch和skew batch。

2. 创建用于存储内部表元组的临时batch文件。

   在此示例中，创建了三个batch文件，因为内部表将被四个batch分割。

3. 对内表的第一个元组执行构建操作。

   细节描述如下：

   1. 如果应将第一个元组插入skew batch中，请执行此操作; 否则，继续2。

      在上面解释的示例中，如果第一个元组是前10％的客户之一，则将其插入到skew batch中。

   2. 计算第一个元组的哈希键，然后插入相应的batch。

4. 对内表的剩余元组执行构建操作。

**图3.27 混合散列的探测阶段的第一轮**

![](img/fig-3-27.png)

1. 创建用于存储外部表元组的临时batch文件。

2. 如果第一个元组的MCV值很大，则使用skew batch执行探测操作; 否则，进入7。

   在上面解释的示例中，如果第一个元组是前10％客户的购买数据，则将其与skew batch中的元组进行比较。

1. 执行第一个元组的探测操作。

   根据第一个元组的哈希键值，执行以下过程：

   如果第一个元组属于Batch_0，则执行探测操作。

   否则，插入相应的batch。

2. 从外表的其余元组执行探测操作。 请注意，在该示例中，外表的70％的元组已由第一轮中的skew处理。

**图3.28. 构建和探测阶段的第二轮**

![](img/fig-3-28.png)

1. 删除skew batch 并清除Batch_0以准备第二轮。
2. 从batch文件'batch_1_in'执行构建操作。
3. 对存储在batch文件'batch_1_out'中的元组执行探测操作。

**图3.29 构建和探测阶段的第三轮以及后面几轮**

![](img/fig-3-29.png)

1. 使用batch文件'batch_2_in'和'batch_2_out'执行构建和探测操作。
2. 使用batch文件'batch_3_in'和'batch_3_out'执行构建和探测操作。

### 3.5.4连接访问路径和连接节点

#### 3.5.4.1 连接访问路径

嵌套循环连接的访问路径是JoinPath结构，其他连接访问路径，比如MergePath和HashPath基于它实现。

以下展示了所有连接访问路径，但没有解释。

**图3.30 Join访问路径**

![](img/fig-3-30.png)

#### 3.5.4.2 连接节点

本小节显示了三个没有解释的连接节点： NestedLoopNode，MergeJoinNode 和 HashJoinNode。 它们基于 JoinNode实现。

```c
/* ----------------
 *		Join node
 *
 * jointype:	rule for joining tuples from left and right subtrees
 * joinqual:	qual conditions that came from JOIN/ON or JOIN/USING
 *				(plan.qual contains conditions that came from WHERE)
 *
 * When jointype is INNER, joinqual and plan.qual are semantically
 * interchangeable.  For OUTER jointypes, the two are *not* interchangeable;
 * only joinqual is used to determine whether a match has been found for
 * the purpose of deciding whether to generate null-extended tuples.
 * (But plan.qual is still applied before actually returning a tuple.)
 * For an outer join, only joinquals are allowed to be used as the merge
 * or hash condition of a merge or hash join.
 * ----------------
 */
typedef struct Join
{
	Plan		plan;
	JoinType	jointype;
	List	   	*joinqual;	/* JOIN quals (in addition to plan.qual) */
} Join;
/* ----------------
 *		nest loop join node
 *
 * The nestParams list identifies any executor Params that must be passed
 * into execution of the inner subplan carrying values from the current row
 * of the outer subplan.  Currently we restrict these values to be simple
 * Vars, but perhaps someday that'd be worth relaxing.  (Note: during plan
 * creation, the paramval can actually be a PlaceHolderVar expression; but it
 * must be a Var with varno OUTER_VAR by the time it gets to the executor.)
 * ----------------
 */
typedef struct NestLoop
{
	Join	   join;
	List	   *nestParams;		/* list of NestLoopParam nodes */
} NestLoop;

typedef struct NestLoopParam
{
	NodeTag	   type;
	int	   paramno;		/* number of the PARAM_EXEC Param to set */
	Var	   *paramval;		/* outer-relation Var to assign to Param */
} NestLoopParam;
/* ----------------
 *		merge join node
 *
 * The expected ordering of each mergeable column is described by a btree
 * opfamily OID, a collation OID, a direction (BTLessStrategyNumber or
 * BTGreaterStrategyNumber) and a nulls-first flag.  Note that the two sides
 * of each mergeclause may be of different datatypes, but they are ordered the
 * same way according to the common opfamily and collation.  The operator in
 * each mergeclause must be an equality operator of the indicated opfamily.
 * ----------------
 */
typedef struct MergeJoin
{
	Join	 join;
	List	 *mergeclauses;		/* mergeclauses as expression trees */
	/* these are arrays, but have the same length as the mergeclauses list: */
	Oid	 *mergeFamilies;	/* per-clause OIDs of btree opfamilies */
	Oid	 *mergeCollations;	/* per-clause OIDs of collations */
	int	 *mergeStrategies;	/* per-clause ordering (ASC or DESC) */
	bool	 *mergeNullsFirst;	/* per-clause nulls ordering */
} MergeJoin;
/* ----------------
 *		hash join node
 * ----------------
 */
typedef struct HashJoin
{
	Join	join;
	List	*hashclauses;
} HashJoin;
```



## 3.6 创建多表查询的计划树

在本节中，将解释创建多表查询的计划树的过程。

### 3.6.1 预处理

`planner.c`中定义的`subquery_planner()`函数执行预处理。第3.3.1节已经描述了单表查询的预处理。在该子部分中，将描述多表查询的预处理；然而，虽然有很多，但只描述了一些部分。

1. 规划和转换CTE

   如果存在WITH列表，则planner通过`SS_process_ctes()`函数处理每个WITH查询。

2. 上拉子查询

   如果FROM子句具有子查询并且它没有`GROUP BY`，`HAVING`，`ORDER BY`，`LIMIT`和`DISTINCT`子句，并且它也不使用INTERSECT或EXCEPT，则planner将通过`pull_up_subqueries()`函数转换为连接形式。

   例如，下面显示的包含FROM子句中的子查询的查询可以转换为自然连接查询。不用说，这种转换是在查询树中完成的。

   ```sql
   testdb=# SELECT * FROM tbl_a AS a, (SELECT * FROM tbl_b) as b WHERE a.id = b.id;
   	 	       	     ↓
   testdb=# SELECT * FROM tbl_a AS a, tbl_b as b WHERE a.id = b.id;
   ```

1. 将Outer Join转换为Inner Join

   如果可能，planner将Outer Join查询转换为Inner Join查询。

### 3.6.2 得到最小代价路径

​	为了获得最佳计划树，planner必须考虑所有索引的组合和连接方法的可能性。 这是一个非常昂贵的过程，如果表的数量超过一定水平，由于组合爆炸，这将是不可行的。 幸运的是，如果表的数量小于12，那么planner可以通过应用动态规划来获得最佳计划。 否则，计划器使用遗传算法。如下

> #### 基因查询优化器
>
> 当执行连接多个表的查询时，将需要大量时间来优化查询计划。 为了解决这种情况，PostgreSQL实现了一个有趣的功能：基因查询优化器。 这是一种在合理时间内确定合理计划的近似算法。 因此，在查询优化阶段，如果连接表的数量高于参数geqo_threshold指定的阈值（默认值为12），PostgreSQL将使用遗传算法生成查询计划。

通过动态规划确定最佳计划树可以通过以下步骤解释：

- 第一层

  获得每张表最便宜的路径; 最便宜的路径存储在相应的RelOptInfo中。

- 第二层

  获取从所有表中选择两个的每个组合的最便宜路径。

  例如，如果有两个表A和B，则获得表A和B中最便宜的连接路径，这是最终答案。

  在下文中，两个表的RelOptInfo由{A，B}表示。

  如果有三个表，则为{A，B}，{A，C}和{B，C}中的每一个获取最便宜的路径。

- 第三层之后

  继续进行相同的处理，直到层级等于表数量。

  这样，在每个级别获得最便宜的部分问题路径，并用于获得上层的计算结果。 这使得有效地计算最便宜的计划树成为可能。

**图3.31 如何使用动态规划获得最便宜的访问路径**

![](img/fig-3-31.png)

在下文中，描述了planner如何获得以下查询的最便宜的计划的过程。

```sql
testdb=# \d tbl_a
     Table "public.tbl_a"
 Column |  Type   | Modifiers 
--------+---------+-----------
 id     | integer | not null
 data   | integer | 
Indexes:
    "tbl_a_pkey" PRIMARY KEY, btree (id)

testdb=# \d tbl_b
     Table "public.tbl_b"
 Column |  Type   | Modifiers 
--------+---------+-----------
 id     | integer | 
 data   | integer | 

testdb=# SELECT * FROM tbl_a AS a, tbl_b AS b WHERE a.id = b.id AND b.data < 400;
```

#### 3.6.2.1 第一层预处理

​	在第一层中，planner创建RelOptInfo结构并估计查询中每个关系的最便宜成本。 在那里，RelOptInfo结构被添加到此查询的PlannerInfo的simple_rel_arrey中。

**图3.32 在Level 1中处理后的PlannerInfo和RelOptInfo**

![](img/fig-3-32.png)

​	tbl_a的RelOptInfo有三个访问路径，它们被添加到RelOptInfo的路径列表中，它们链接到三个最便宜的成本路径，即最便宜的启动（成本）路径，最便宜的总（成本）路径， 和最便宜的参数化（成本）路径。 由于最便宜的启动和总成本路径是显而易见的，因此将描述最便宜的参数化索引扫描路径的成本。

​	如3.5.1.3节所述，planner考虑使用索引嵌套循环连接的参数化路径（并且很少使用外部索引扫描进行索引归并连接）。 最便宜的参数化成本是估计的参数化路径的最便宜的成本。

​	tbl_b的RelOptInfo仅具有顺序扫描访问路径，因为tbl_b没有相关索引。

#### 3.6.2.2 第二层预处理

在第二层中，创建RelOptInfo结构并将其添加到PlannerInfo的join_rel_list。 然后，估计所有可能的连接路径的成本，并且选择其总成本最便宜的最佳访问路径。 RelOptInfo将最佳访问路径存储为最便宜的总成本路径。 参见图3.33。

**图3.33 在Level 2中处理后的PlannerInfo和RelOptInfo**

![](img/fig-3-33.png)

表3.1显示了此示例中的连接访问路径的所有组合。 该示例的查询是等值连接类型; 因此，估计了三种连接算法。 为方便起见，引入了一些访问路径的符号：

- `SeqScanPath（table）`表示表的顺序扫描路径。
- `Materialized-> SeqScanPath（table）`表示表的物化顺序扫描路径。
- `IndexScanPath（table，attribute）`表示按表的属性的索引扫描路径。
- `ParameterizedIndexScanPath（table，attribute1，attribute2）`表示表的attribute1的参数化索引路径，并由外表的attribute2参数化。

**表 3.1 此示例中的所有连接访问路径组合**

**嵌套循环连接**

| 序号 | 外表路径                | 内表路径                                        | 备注                         |
| ---- | ----------------------- | ----------------------------------------------- | ---------------------------- |
| 1    | SeqScanPath(tbl_a)      | SeqScanPath(tbl_b)                              |                              |
| 2    | SeqScanPath(tbl_a)      | Materialized->SeqScanPath(tbl_b)                | 物化嵌套循环链接             |
| 3    | IndexScanPath(tbl_a,id) | SeqScanPath(tbl_b)                              | 嵌套循环连接，走外表索引     |
| 4    | IndexScanPath(tbl_a,id) | Materialized->SeqScanPath(tbl_b)                | 物化嵌套循环连接，走外表索引 |
| 5    | SeqScanPath(tbl_b)      | SeqScanPath(tbl_a)                              |                              |
| 6    | SeqScanPath(tbl_b)      | Materialized->SeqScanPath(tbl_a)                | 物化嵌套循环连接             |
| 7    | SeqScanPath(tbl_b)      | ParametalizedIndexScanPath(tbl_a, id, tbl_b.id) | 索引嵌套循环连接             |

**归并连接**

| 序号 | 外表路径                | 内表路径           | 备注                 |
| ---- | ----------------------- | ------------------ | -------------------- |
| 1    | SeqScanPath(tbl_a)      | SeqScanPath(tbl_b) |                      |
| 2    | IndexScanPath(tbl_a,id) | SeqScanPath(tbl_b) | 用外表索引做归并连接 |
| 3    | SeqScanPath(tbl_b)      | SeqScanPath(tbl_a) |                      |

**哈希连接**

| 序号 | 外表路径           | 内表路径           | 备注 |
| ---- | ------------------ | ------------------ | ---- |
| 1    | SeqScanPath(tbl_a) | SeqScanPath(tbl_b) |      |
| 2    | SeqScanPath(tbl_b) | SeqScanPath(tbl_a) |      |

例如，在嵌套循环连接中，估计了七个连接路径。 第一个表示外部和内部路径分别是$\verb|tbl_a|$和$\verb|tbl_b|$的顺序扫描路径; 第二个表示外部路径是$\verb|tbl_a|$的顺序扫描路径，内部路径是$\verb|tbl_b|$的物化顺序扫描路径，诸如此类。

计划器最终从估计的连接路径中选择最便宜的访问路径，并且将最便宜的路径添加到$\verb|RelOptInfo{tbl_a,tbl_b}|$的路径列表中，参见图3.33。

在此示例中，如下面EXPLAIN的结果所示，计划器选择内部和外部表为tbl_b和tbl_c的散列连接。

```
testdb=# EXPLAIN  SELECT * FROM tbl_b AS b, tbl_c AS c WHERE c.id = b.id AND b.data < 400;
                              QUERY PLAN                              
----------------------------------------------------------------------
 Hash Join  (cost=90.50..277.00 rows=400 width=16)
   Hash Cond: (c.id = b.id)
   ->  Seq Scan on tbl_c c  (cost=0.00..145.00 rows=10000 width=8)
   ->  Hash  (cost=85.50..85.50 rows=400 width=8)
         ->  Seq Scan on tbl_b b  (cost=0.00..85.50 rows=400 width=8)
               Filter: (data < 400)
(6 rows)
```

### 3.6.3 得到三表查询上最小代价路径

获取涉及三个表的查询的最小开销路径如下：

```sql
testdb=# \d tbl_a
     Table "public.tbl_a"
 Column |  Type   | Modifiers 
--------+---------+-----------
 id     | integer | 
 data   | integer | 

testdb=# \d tbl_b
     Table "public.tbl_b"
 Column |  Type   | Modifiers 
--------+---------+-----------
 id     | integer | 
 data   | integer | 

testdb=# \d tbl_c
     Table "public.tbl_c"
 Column |  Type   | Modifiers 
--------+---------+-----------
 id     | integer | not null
 data   | integer | 
Indexes:
    "tbl_c_pkey" PRIMARY KEY, btree (id)

testdb=# SELECT * FROM tbl_a AS a, tbl_b AS b, tbl_c AS c 
testdb-#                WHERE a.id = b.id AND b.id = c.id AND a.data < 40;
```

- 第一层：

  计划器估计所有表中开销最小的路径，并将该信息存储在相应的*RelOptInfos*结构中：$\verb|{tbl_a}|$，$\verb|{tbl_b}|$，$\verb|{tbl_c}|$中。

- 第二层：

  计划器选择三对表的所有组合并估计每种组合最便宜的路径;然后，规划器将信息存储在相应的*RelOptInfos*结构中：$\verb|{tbl_a,tbl_b}|$，$\verb|{tbl_b,tbl_c}|$和$\verb|{tbl_a,tbl_c}|$中。

- 第三层：

计划器最终使用已经获得的RelOptInfos获得最便宜的路径。更确切地说，计划器考虑RelOptInfos的三种组合：$\verb|{tbl_a,{tbl_b,tbl_c}}|$，$\verb|{tbl_b,{tbl_a,tbl_c}}|$和$\verb|{tbl_c,{tbl_a,tbl_b}}|$，因而$\verb|{tbl_a,tbl_b,tbl_c}|$如下所示：
$$
\begin{equation}
  \{\verb|tbl_a|,\verb|tbl_b|,\verb|tbl_c|\} = \\ min (\{\verb|tbl_a|,\{\verb|tbl_b|,\verb|tbl_c|\}\}, \{\verb|tbl_b|,\{\verb|tbl_a|,\verb|tbl_c|\}\}, \{\verb|tbl_c|,\{\verb|tbl_a|,\verb|tbl_b|\}\}).
  \end{equation}
$$

然后，计划器估算其中所有可能的连接路径的成本。

在$\verb|RelOptInfo{tbl_c,{tbl_a,tbl_b}}|$中，计划器估计$\verb|tbl_c|$和$\verb|{tbl_a,tbl_b}|$最便宜路径的所有组合，它是内部和外部表分别为$\verb|tbl_a|$和$\verb|tbl_b|$的散列连接。这个例子。估计的连接路径将包含三种连接路径及其变体，如前一小节所示，即嵌套循环连接及其变体，归并连接及其变体以及散列连接。

计划器以相同的方式处理$\verb|RelOptInfos{tbl_a,{tbl_b,tbl_c}}|$和$\verb|{tbl_b,{tbl_a,tbl_c}}|$，并最终从所有估计的路径中选择开销最小的访问路径。

此查询的`EXPLAIN`命令的结果如下所示：

  ![](img/fig-3-34.png)

最外层的连接是索引的嵌套循环连接（第5行）; 内部参数化索引扫描显示在第13行中，外部关系是散列连接的结果，其内表和外表分别为$\verb|tbl_b|$和$\verb|tbl_a|$（第7-12行）。 因此，执行程序首先执行$\verb|tbl_a|$和$\verb|tbl_b|$的散列连接，然后执行索引嵌套循环连接。

## 参考文献

- [1] Abraham Silberschatz, Henry F. Korth, and S. Sudarshan, "[Database System Concepts](https://www.amazon.com/dp/0073523321)", McGraw-Hill Education, ISBN-13: 978-0073523323
- [2] Thomas M. Connolly, and Carolyn E. Begg, "[Database Systems](https://www.amazon.com/dp/0321523067)", Pearson, ISBN-13: 978-0321523068