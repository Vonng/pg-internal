# 第八章 缓存管理器

[TOC]

**缓冲管理器（Buffer Manager）管**理着共享内存和持久存储之间的数据传输，对于DBMS的性能有着重要的影响。PostgreSQL的缓冲管理器十分高效。

本文介绍了PostgreSQL的缓冲管理器。第一节介绍了**缓冲管理器（Buffer Manager）**的概况，后续的章节分别介绍以下内容：

+ 缓冲管理器的架构

+ 缓冲管理器的锁

+ 缓冲管理器是如何工作的

+ 环形缓冲区

+ 脏页的刷写

  

  **图8.1 缓冲管理器，存储，后端进程之间的关系**

  ![C76949F9-6362-4AA8-A5FB-9537C9A9B970](img/fig-8-01.png)

## 8.1 概览

本节介绍了一些关键概念，有助于理解后续章节。

### 8.1.1 缓冲区管理器的结构

PostgreSQL缓冲区管理器包含缓冲表，缓冲区描述符和缓冲池，这些将在下一节中介绍。 缓冲池层存储数据文件页面，例如表和索引，以及自由空间映射和可见性映射。 缓冲池是一个阵列，即每个槽存储一页数据文件。 缓冲池数组的索引称为buffer_ids。

第8.2和8.3节描述了缓冲区管理器内部的细节。

### 8.1.2 Buffer Tag

​	在PostgreSQL中，可以为所有数据文件的每个页面分配唯一标记，即缓冲标记。 当缓冲区管理器收到请求时，PostgreSQL使用所需页面的buffer_tag。

​	buffer_tag包含三个值：relfilenode和其页面所属关系的fork编号，以及其页面的块编号。 例如，buffer_tag'（17721,0,7）'标识第七个块中的页面，其OID和分叉号分别为17721和0。 （表的分叉数为0.）类似地，buffer_tag'（17721,1,3）'标识自由空间映射的第三个块中的页面，其OID和分叉号分别为17721和1。 （自由空间映射的分叉数为1）。

#### 8.1.3 后台进程如何读取数据页

本小节描述了后端进程如何从缓冲区管理器中读取页面（图8.2）。

**图8.2 后台进程如何读取数据页** 

![](img/fig-8-02.png)

（1）当读取表或索引页时，后端进程将包含页的buffer_tag的请求发送到缓冲区管理器。
（2）缓冲区管理器返回存储所请求页面的槽的buffer_ID。 如果请求的页面未存储在缓冲池中，则缓冲区管理器将页面从永久存储器加载到其中一个缓冲池槽，然后返回buffer_ID的槽。
（3）后端进程访问buffer_ID的槽（以读取所需的页面）。
当后端进程修改缓冲池中的页面时（例如，通过插入元组），尚未刷新到存储的已修改页面被称为脏页面。

8.4节描述了缓冲管理器的工作原理。

#### 8.1.4 页面置换算法

​	当所有缓冲池槽都被占用但未存储所请求的页面时，缓冲区管理器必须在缓冲池中选择一个页面，该页面将被请求的页面替换。 通常，在计算机科学领域中，页面选择算法被称为页面替换算法，并且所选择的页面被称为受害者页面。

​	自计算机科学出现以来，页面替换算法的研究一直在进行; 因此，先前已经提出了许多替换算法。 从版本8.1开始，PostgreSQL使用时钟扫描，因为它比以前版本中使用的LRU算法更简单，更有效。

第8.4.4节描述了时钟扫描的细节。

#### 8.1.5 刷新脏页

​	脏页最终应该被刷新到存储; 但是，缓冲区管理器需要帮助才能执行此任务。 在PostgreSQL中，两个后台进程checkpointer和后台编写器负责此任务。

8.6节描述了checkpointer和background writer。

> 直接IO（DirectIO）:
>
> PostgreSQL不支持直接I/O，但有时会讨论它。 如果您想了解更多详细信息，请参阅pgsql-ML中的讨论和这篇文章。
>
> https://lwn.net/Articles/580542/
>
> https://www.postgresql.org/message-id/529E267F.4050700@agliodbs.com

### 8.2 缓冲管理器的结构

PostgreSQL缓冲区管理器包含三层，即缓冲区表，缓冲区描述符和缓冲池（图8.3）：

**图8.3 缓冲区管理器的三层结构**

![](img/fig-8-03.png)

+ 缓冲池是一个数组。 每个插槽都存储一个数据文件页面。 数组槽的索引称为buffer_ids。

+ 缓冲区描述符层是缓冲区描述符的数组。 每个描述符与缓冲池槽一一对应，并且在相应的槽中保存所存储页面的元数据。

  请注意，为方便起见，术语“缓冲区描述符层”已被采用，它仅用于本文档。

+ 缓冲表是哈希表，其存储所存储页面的buffer_tags与保存所存储页面的相应元数据的描述符的buffer_id之间的关系。

这些层在以下小节中详细描述。

#### 8.2.1 缓冲表

​	缓冲表可以在逻辑上分为三个部分：散列函数，散列桶时隙和数据条目（图8.4）。

​	内置哈希函数将buffer_tags映射到哈希桶槽。 即使散列桶时隙的数量大于缓冲池时隙的数量，也可能发生冲突。 因此，缓冲表使用单独的链接和链接列表方法来解决冲突。 当数据条目映射到同一个存储槽时，此方法将条目存储在同一个链表中，如图8.4所示。

**图8.4 Buffer Table**

![](img/fig-8-04.png)

​	数据条目包括两个值：页面的buffer_tag，以及保存页面元数据的描述符的buffer_id。例如，数据条目“ *Tag_A，id = 1* ”表示具有buffer_id *1*的缓冲区描述符存储用*Tag_A*标记的页面的元数据。

> 哈希函数
>
> 散列函数是[calc_bucket（）](https://doxygen.postgresql.org/dynahash_8c.html#ae802f2654df749ae0e0aadf4b5c5bcbd)和 [hash（）](https://doxygen.postgresql.org/rege__dfa_8c.html#a6aa3a27e7a0fc6793f3329670ac3b0cb)的复合函数。 以下是它作为伪函数的表示。
>
> ```
> uint32 bucket_slot = calc_bucket （unsigned hash （BufferTag buffer_tag ），uint32 bucket_size ） 
> ```

请注意，此处不解释基本操作（查找，插入和删除数据条目）。这些是非常常见的操作，将在以下各节中进行说明。

#### 8.2.2 缓冲区描述符 

​	缓冲区描述符的结构在本小节中描述，缓冲区描述符层在下一小节中描述。

​	缓冲区描述符将存储页面的元数据保存在相应的缓冲池槽中。缓冲区描述符结构由[BufferDesc](javascript:void(0))结构定义。虽然这个结构有很多领域，但主要有以下几种：

- **tag**将存储页面的*buffer_tag*保存在相应的缓冲池槽中（缓冲区标记在[8.1.2节中](http://www.interdb.jp/pg/pgsql08.html#_8.1.2.)定义 ）。

- **buffer_id**标识描述符（相当于相应缓冲池槽的*buffer_id*）。

- **refcount**保存当前访问相关存储页面的PostgreSQL进程数。它也被称为**引脚数**。当PostgreSQL进程访问存储的页面时，其引用计数必须递增1（refcount ++）。访问该页面后，其引用计数必须减少1（refcount--）。 
  当refcount为零时，即当前未访问相关的存储页面时，页面将被**取消固定** ; 否则它被**固定**。

- **usage_count**保存关联存储页面加载到相应缓冲池槽后的访问次数。请注意，usage_count用于页面替换算法（[第8.4.4节](http://www.interdb.jp/pg/pgsql08.html#_8.4.4.)）。

- **context_lock**和**io_in_progress_lock**是轻量级锁，用于控制对相关存储页面的访问。这些字段在[第8.3.2节](http://www.interdb.jp/pg/pgsql08.html#_8.3.2.)中描述。

- flags

  可以保存相关存储页面的几种状态。主要国家如下：

  - **脏位**表示存储的页面是否脏。
  - **有效位**指示是否可以读取或写入存储的页面（有效）。例如，如果该位*有效*，则相应的缓冲池槽存储页面，该描述符（有效位）保存页面元数据; 因此，可以读取或写入存储的页面。如果此位*无效*，则此描述符不保存任何元数据; 这意味着无法读取或写入存储的页面，或者缓冲区管理器正在替换存储的页面。
  - **io_in_progress位**指示缓冲区管理器是否正在从存储器读取/写入关联的页面。换句话说，该位指示单个进程是否保存该描述符的io_in_progress_lock。

- **freeNext**是一个指向下一个描述符产生一个*空闲列表*，其在下一小节中描述。

> 结构*BufferDesc*在[src / include / storage / buf_internals.h中定义](https://github.com/postgres/postgres/blob/master/src/include/storage/buf_internals.h)。

为了简化以下描述，定义了三个描述符状态：

- **空**：当相应的缓冲池槽不存储页面（即*引用计数*和*usage_count*是0），该描述符的状态是*空的*。
- **固定**：当相应的缓冲池槽存储页面和任何的PostgreSQL进程正在访问的页面（即，*引用计数*和*usage_count*大于或等于1），该缓冲区描述符的状态被*固定*。
- **取消固定**：当相应的缓冲池槽存储页面但没有PostgreSQL进程正在访问页面时（即*usage_count*大于或等于1，但*refcount*为0），则*取消固定*此缓冲区描述符的状态。

每个描述符将具有上述状态之一。描述符状态相对于特定条件而改变，这将在下一小节中描述。

在下图中，缓冲区描述符的状态由彩色框表示。

- ​    （白色）*空*
- ​    （蓝色）*固定*
- ​    （青色）未*固定*

另外，脏页面表示为“X”。例如，未固定的脏描述符由 X 表示。

### 8.2.3 缓冲区描述符层

​	缓冲区描述符的集合形成一个数组。在本文档中，该数组称为*缓冲区描述符层*。

​	当PostgreSQL服务器启动时，所有缓冲区描述符的状态为*空*。在PostgreSQL中，这些描述符包含一个名为**freelist**的链表（图8.5）。

**图8.5 缓冲管理器初始状态**

![](img/fig-8-05.png)

> 请注意，**freelist**在PostgreSQL是从完全不同的概念*freelist*中的Oracle。PostgreSQL的freelist只是空缓冲区描述符的链表。在PostgreSQL中，*自由空间映射*（在[第5.3.4节](http://www.interdb.jp/pg/pgsql05.html#_5.3.4.)中描述）充当Oracle中自由列表的相同角色。

图8.6显示了第一页的加载方式。

- （1）从空闲列表的顶部检索空描述符，并将其固定（即将其refcount和usage_count增加1）。
- （2）在缓冲表中插入新条目，该条目保存第一页的标记和检索到的描述符的buffer_id之间的关系。
- （3）将新页面从存储器加载到相应的缓冲池槽位。
- （4）将新页面的元数据保存到检索到的描述符中。

第二页和后续页面以类似方式加载。其他细节在[第8.4.2节](http://www.interdb.jp/pg/pgsql08.html#_8.4.2.)中提供。

**图8.6 加载第一页**

![](img/fig-8-06.png)

​	从空闲列表中检索的描述符始终保存页面的元数据。换句话说，继续使用非空描述符不返回空闲列表。但是，相关描述符将再次添加到空闲列表中，并且当发生以下任一情况时，描述符状态将变为“空”：

1. 表或索引已被删除。
2. 数据库已被删除。
3. 已使用VACUUM FULL命令清除表或索引。

> 为什么用freelist来维护空描述符？
>
> 制作freelist的原因是为了立即获得第一个描述符。这是动态内存资源分配的通常做法。请参阅[此说明](https://en.wikipedia.org/wiki/Free_list)。

缓冲区描述符层包含无符号的32位整数变量，即**nextVictimBuffer**。此变量用于[第8.4.4节中](http://www.interdb.jp/pg/pgsql08.html#_8.4.4.)描述的页面替换算法。

#### 8.2.4 缓冲池

​	缓冲池是一个存储数据文件页面的简单数组，例如表和索引。缓冲池阵列的索引称为*buffer_id*。

​	缓冲池插槽大小为8 KB，等于页面大小。因此，每个槽可以存储整个页面。

## 8.3 缓冲区管理器锁

缓冲区管理器使用许多锁来实现许多不同的目的。本节介绍后续章节中解释所需的锁定。

> 请注意，本节中描述的锁是缓冲区管理器的同步机制的一部分; 他们根本**不**涉及到任何SQL语句和SQL选项

### 8.3.1 缓冲表锁

**BufMappingLock**保护整个缓冲表的数据完整性。它是一种轻量级锁，可用于共享和独占模式。在缓冲表中搜索条目时，后端进程拥有共享的BufMappingLock。插入或删除条目时，后端进程持有独占锁。

BufMappingLock被拆分为分区以减少缓冲表中的争用（默认为128个分区）。每个BufMappingLock分区都保护相应哈希桶槽的部分。

图8.7显示了拆分BufMappingLock的典型示例。两个后端进程可以在独占模式下同时保存各自的BufMappingLock分区，以便插入新的数据条目。如果BufMappingLock是单个系统范围的锁，则两个进程都应该等待另一个进程的处理，具体取决于哪个进程处理。

**图8.7 两个进程同时以独占模式获取BufMappingLock的相应分区以插入新数据条目**

![](img/fig-8-07.png)

缓冲表需要许多其他锁。例如，缓冲表内部使用旋转锁来删除条目。但是，省略了对这些其他锁的描述，因为在本文档中不需要它们。

> 默认情况下，BufMappingLock已分为16个单独的锁，直到版本9.4。

### 8.3.2 每个缓冲区描述符的锁

每个缓冲区描述符使用两个轻量级锁，**content_lock**和**io_in_progress_lock**来控制对相应缓冲池槽中存储页面的访问。检查或更改自己字段的值时，使用自旋锁。

#### 8.3.2.1 content_lock

+ content_lock是一个强制访问限制的典型锁。它可以在*共享*和*独占*模式下使用。

  当读取页面时，后端进程获取存储页面的缓冲区描述符的共享content_lock。

  但是，执行以下操作之一时会获取独占的content_lock：

  - 将行（即元组）插入存储的页面或更改存储页面中元组的t_xmin / t_xmax字段（t_xmin和t_xmax在[第5.2节](http://www.interdb.jp/pg/pgsql05.html#_5.2.)中描述;简单地说，当删除或更新行时，相关元组的这些字段将被更改） 。
  - 物理地移除元组或压缩存储页面上的自由空间（由真空处理和HOT执行，分别在[第6章和第](http://www.interdb.jp/pg/pgsql06.html) 7 [章](http://www.interdb.jp/pg/pgsql06.html)中描述）。
  - 冻结存储页面中的元组（冻结在[第5.10.1 ](http://www.interdb.jp/pg/pgsql05.html#_5.10.1.)[节](http://www.interdb.jp/pg/pgsql06.html#_6.3.)和[第6.3 ](http://www.interdb.jp/pg/pgsql06.html#_6.3.)[节中](http://www.interdb.jp/pg/pgsql05.html#_5.10.1.)描述）。

  官方[README](https://github.com/postgres/postgres/blob/master/src/backend/storage/buffer/README) 文件显示更多详细信息。

#### 8.3.2.2 io_in_progress_lock

io_in_progress锁用于等待缓冲区上的I / O完成。当PostgreSQL进程从/向存储加载/写入页面数据时，该进程在访问存储时保持对应描述符的独占io_in_progress锁定。

#### 8.3.2.3 自旋锁(spinlock)

当检查或更改标志或其他字段（例如refcount和usage_count）时，使用自旋锁。螺旋锁使用的两个具体示例如下：

1. 以下显示如何**固定**缓冲区描述符：

- 1. 获取缓冲区描述符的自旋锁。
  2. 将其引用计数和usage_count的值增加1。
  3. 松开自旋锁。

- ```
  LockBufHdr(bufferdesc);    /* Acquire a spinlock */
  bufferdesc->refcont++;
  bufferdesc->usage_count++;
  UnlockBufHdr(bufferdesc); /* Release the spinlock */
  ```

当一些标志位，比如refcount和usage_count被检查或者更改的时候，使用这个锁，在PostgreSQL9.6中用原子操作替代了这个锁；

2. 以下显示如何将脏位设置为“1”：

- 1. 获取缓冲区描述符的自旋锁。
  2. 使用按位操作将脏位设置为“1”。
  3. 松开自旋锁。

- ```
  #define BM_DIRTY             (1 << 0)    /* data needs writing */
  #define BM_VALID             (1 << 1)    /* data is valid */
  #define BM_TAG_VALID         (1 << 2)    /* tag is assigned */
  #define BM_IO_IN_PROGRESS    (1 << 3)    /* read or write in progress */
  #define BM_JUST_DIRTIED      (1 << 5)    /* dirtied since write started */

  LockBufHdr(bufferdesc);
  bufferdesc->flags |= BM_DIRTY;
  UnlockBufHdr(bufferdesc);
  ```

- 以相同的方式执行改变其他位。

> * 用原子操作替换缓冲区管理器spinlock*
>
> 在版本9.6中，缓冲区管理器的自旋锁将被替换为原子操作。查看[commitfest的结果](https://commitfest.postgresql.org/9/408/)。如果您想了解详细信息，请参阅[此讨论](http://www.postgresql.org/message-id/flat/2400449.GjM57CE0Yg@dinodell#2400449.GjM57CE0Yg@dinodell)。

## 8.4 缓冲管理器的工作原理

本节介绍缓冲区管理器的工作原理。当后端进程想要访问所需页面时，它会调用*ReadBufferExtended*函数。

*ReadBufferExtended*函数 的行为取决于三种逻辑情况。每个案例在以下小节中描述。此外，PostgreSQL *时钟扫描*页面替换算法在最后一小节中描述。

### 8.4.1 访问存储在缓冲池中的页面

首先，描述最简单的情况，即所需页面已经存储在缓冲池中。在这种情况下，缓冲区管理器执行以下步骤：

- （1）创建所需页面的*buffer_tag*（在该示例中，buffer_tag是'Tag_C'）并使用散列函数计算包含所创建的*buffer_tag*的关联条目的*散列桶时隙*。**
- （2）以共享模式获取覆盖获得的哈希桶槽的BufMappingLock分区（该锁将在步骤（5）中释放）。
- （3）查找标签为“Tag_C”的条目，并从条目中获取*buffer_id*。在此示例中，buffer_id为2。
- （4）将缓冲区描述符固定为buffer_id 2，即描述符的refcount和usage_count增加1（[第8.3.2节](http://www.interdb.jp/pg/pgsql08.html#_8.3.2.)描述了固定）。
- （5）释放BufMappingLock。
- （6）使用buffer_id 2访问缓冲池槽。

**图8.8 访问存储在缓冲池中的页面。**

![](img/fig-8-08.png)

然后，当从缓冲池槽中的页面读取行时，PostgreSQL进程获取相应缓冲区描述符的*共享content_lock*。因此，缓冲池槽可以由多个进程同时读取。

当向页面插入（和更新或删除）行时，Postgres进程获取相应缓冲区描述符的*独占content_lock*（请注意，页面的脏位必须设置为“1”）。

访问页面后，相应缓冲区描述符的引用计数值减1。

### 8.4.2 将页面从存储加载到空槽

在第二种情况下，假设所需页面不在缓冲池中，并且空闲列表具有空闲元素（空描述符）。在这种情况下，缓冲区管理器执行以下步骤：

- （1）查找缓冲区表（我们假设找不到它）。
  - 1.创建所需页面的buffer_tag（在此示例中，buffer_tag为'Tag_E'）并计算哈希桶槽。
  - 2.以共享模式获取BufMappingLock分区。
  - 3.查找缓冲区表（根据假设未找到）。
  - 4.释放BufMappingLock。
- （2）从*空闲列表中*获取*空缓冲区描述符*，并将其固定。在该示例中，获得的描述符的buffer_id是4。
- （3）以*独占*模式获取BufMappingLock分区（此锁定将在步骤（6）中释放）。
- （4）创建一个包含buffer_tag'Tag_E'和buffer_id 4的新数据条目; 将创建的条目插入缓冲区表。
- （5）使用buffer_id 4将所需的页面数据从存储器加载到缓冲池槽，如下所示：
  - 1.获取相应描述符的独占io_in_progress_lock。
  - 2.将相应描述符的*io_in_progress*位设置为1以防止其他进程访问。
  - 3.将所需的页面数据从存储装载到缓冲池插槽。
  - 4.更改相应描述符的状态; 所述*io_in_progress*位被设置为“0”，并且*有效*位被设置为“1”。
  - 5.释放io_in_progress_lock。
- （6）释放BufMappingLock。
- （7）使用buffer_id 4访问缓冲池槽。

**图8.9。将页面从存储装载到空插槽**

![](img/fig-8-09.png)

### 8.4.3 将页面从存储加载到受害者缓冲池插槽

在这种情况下，假设所有缓冲池槽都被页面占用，但不存储所需的页面。缓冲区管理器执行以下步骤：

- （1）创建所需页面的buffer_tag并查找缓冲表。在这个例子中，我们假设buffer_tag是'Tag_M'（找不到所需的页面）。

- （2）使用时钟扫描算法选择受害者缓冲池时隙，从缓冲区表中获取包含受害者池时隙的buffer_id的旧条目，并将受害者池时隙固定在缓冲区描述符层中。在此示例中，受害者时隙的buffer_id为5，旧条目为“Tag_F，id = 5”。时钟扫描将在[下一小节中介绍](http://www.interdb.jp/pg/pgsql08.html#_8.4.4.)。

- （3）如果受害者页面数据是脏的，则刷新（写入和fsync）; 否则进入步骤（4）。 

  ​

  在使用新数据覆盖之前，必须将脏页写入存储。刷新脏页面的步骤如下：

  - 1.使用buffer_id 5（在步骤6中释放）获取描述符的共享content_lock和独占io_in_progress锁。
  - 2.更改相应描述符的状态; 所述*io_in_progress*位被设置为“1”和*just_dirtied*位被设置为“0”。
  - 3.根据具体情况，调用*XLogFlush（）*函数将WAL缓冲区上的WAL数据写入当前WAL段文件（详细信息省略; WAL和*XLogFlush*函数在[第9章](http://www.interdb.jp/pg/pgsql09.html)中描述）。
  - 4.将受害者页面数据刷新到存储。
  - 5.更改相应描述符的状态; 所述*io_in_progress*位被设置为“0”和*有效*位被设置为“1”。
  - 6.释放io_in_progress和content_lock锁。

- （4）以独占模式获取覆盖包含旧条目的插槽的旧BufMappingLock分区。

- （5）获取新的BufMappingLock分区并将新条目插入缓冲区表：

  - 1.创建由新buffer_tag'Tag_M'和受害者的buffer_id组成的新条目。
  - 2.获取新的BufMappingLock分区，该分区覆盖包含独占模式中新条目的插槽。
  - 3.将新条目插入缓冲区表。

**图8.10 将页面从存储加载到受害者缓冲池槽**

![](img/fig-8-10.png)

- （6）从缓冲表中删除旧条目，并释放旧的BufMappingLock分区。
- （7）将所需的页面数据从存储器加载到受害者缓冲槽。然后，用buffer_id 5更新描述符的标志; 脏位设置为'0并初始化其他位。
- （8）释放新的BufMappingLock分区。
- （9）使用buffer_id 5访问缓冲池槽。

**图8.11 将页面从存储器加载到受害者缓冲池槽（从图8.10继续）**

![](img/fig-8-11.png)

### 8.4.4 页面位移算法：时钟扫描

本节的其余部分介绍了**时钟扫描**算法。该算法是NFU（非常用）的变种，开销低; 它有效地选择不常使用的页面。

将缓冲区描述符设想为循环列表（图8.12）。nextVictimBuffer是一个无符号的32位整数，它总是指向一个缓冲区描述符并顺时针旋转。伪代码和算法描述如下：

> *伪代码：时钟扫描*
>
> ```
>      WHILE true
> (1)     Obtain the candidate buffer descriptor pointed by the nextVictimBuffer
> (2)     IF the candidate descriptor is unpinned THEN
> (3)	       IF the candidate descriptor's usage_count == 0 THEN
> 	            BREAK WHILE LOOP  /* the corresponding slot of this descriptor is victim slot. */
> 	       ELSE
> 		    Decrease the candidate descriptpor's usage_count by 1
>                END IF
>          END IF
> (4)     Advance nextVictimBuffer to the next one
>       END WHILE 
> (5) RETURN buffer_id of the victim
> ```
>
> - （1）获取*nextVictimBuffer*指向的候选缓冲区描述符。
> - （2）如果*取消固定*候选缓冲区描述符，则进入步骤（3）; 否则，进入步骤（4）。
> - （3）如果候选描述符的*usage_count*为*0*，则选择该描述符的对应时隙作为受害者，并进入步骤（5）; 否则，将此描述符的*usage_count减* 1并继续执行步骤（4）。
> - （4）将nextVictimBuffer推进到下一个描述符（如果最后，回绕）并返回步骤（1）。重复直到找到受害者。
> - （5）返回受害者的buffer_id。

具体的例子如图8.12所示。缓冲区描述符显示为蓝色或青色框，框中的数字显示每个描述符的usage_count。

**图8.12 时钟扫描**

![](img/fig-8-12.png)



- 1）nextVictimBuffer指向第一个描述符（buffer_id 1）; 但是，此描述符被跳过，因为它被固定。
- 2）nextVictimBuffer指向第二个描述符（buffer_id 2）。取消固定此描述符但其usage_count为2; 因此，usage_count减少1并且nextVictimBuffer前进到第三候选。
- 3）nextVictimBuffer指向第三个描述符（buffer_id 3）。取消固定此描述符，其usage_count为0; 因此，这是本轮的受害者。

每当*nextVictimBuffer*扫描未固定的描述符时，其*usage_count*减少1.因此，如果缓冲池中存在未固定的描述符，则此算法始终可以通过旋转*nextVictimBuffer*找到其usage_count为0的*受害者*。



## 8.5 环形缓冲区

​	在读写大表时，PostgreSQL使用**环形缓冲区**而不是缓冲池。*环形缓冲器*是一个很小的临时缓冲区域。当满足下面列出的任何条件时，将在共享内存中分配一个环形缓冲区：

1. 批量读取

   扫描读取的数据大小超过缓冲池四分之一的大小（`shared_buffers/4`），在这种情况下，环形缓冲区大小为*256 KB*。

2. 批量写入

   当执行下面列出的SQL命令时。这种情况下环形缓冲区大小为*16 MB*。

   * `COPY FROM`命令。

   - `CREATE TABLE AS`命令。
   - [CREATE MATERIALIZED VIEW](http://www.postgresql.org/docs/current/static/sql-creatematerializedview.html)或 [REFRESH MATERIALIZED VIEW](http://www.postgresql.org/docs/current/static/sql-refreshmaterializedview.html)命令。
   - [ALTER TABLE](http://www.postgresql.org/docs/current/static/sql-altertable.html)命令。

3. 清理过程

   当autovacuum执行清理过程时。在这种情况下，环形缓冲区大小为256 KB。

分配的环形缓冲区在使用后立即释放。

环形缓冲区的好处是显而易见的。如果后端进程在不使用环形缓冲区的情况下读取大表，则删除缓冲池中的所有存储页会被移除（踢出），因而会导致缓存命中率降低。环形缓冲区可以避免此问题。

> #### 为什么批量读取和真空处理的默认环形缓冲区大小为256 KB？
>
> 为什么256 KB？答案在缓冲区管理器源目录下的[README中](https://github.com/postgres/postgres/blob/master/src/backend/storage/buffer/README)解释。
>
> > 对于顺序扫描，使用256 KB环。这个小到足以适应L2缓存，这使得从OS缓存到共享缓冲区缓存的页面传输效率很高。更少的通常就足够了，但是环必须足够大以容纳扫描中同时固定的所有页面。（剪断）

## 8.6 刷新脏页面

除了替换受害者页面之外，checkpointer和后台写入器进程将脏页面刷新到存储。两个进程都具有相同的功能（刷新脏页）；但是，他们有不同的角色和行为。

checkpointer进程将检查点记录写入WAL段文件，并在检查点开始时刷新脏页。[第9.7节](http://www.interdb.jp/pg/pgsql09.html#_9.7.)描述了检查点以及何时开始。

​	后台写入器的作用是减少存档带来的密集写入影响。后台写入器继续一点一点地刷新脏页面，对数据库活动的影响最小。默认情况下，后台[编写器](http://www.postgresql.org/docs/current/static/runtime-config-resource.html#GUC-BGWRITER-DELAY)每200毫秒唤醒（由[bgwriter_delay](http://www.postgresql.org/docs/current/static/runtime-config-resource.html#GUC-BGWRITER-DELAY)定义）并[最多](http://www.postgresql.org/docs/current/static/runtime-config-resource.html#GUC-BGWRITER-LRU-MAXPAGES)刷新[bgwriter_lru_maxpages](http://www.postgresql.org/docs/current/static/runtime-config-resource.html#GUC-BGWRITER-LRU-MAXPAGES)（默认值为100页）。