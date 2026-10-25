# Cairn 理论 — 断言流、读取时解析、可证明合并

> Cairn（ cairn，石堆）：本地优先的共享记忆数据库。每条记录是一块石头；
> 圈子的共同记忆是石头堆。本文定义其语义、不变量与核心算法。

---

## 0. 问题形式化

给定一组记录员 R（人、机器人、AI 导入器）在各自设备上离线记录事实，
要求：

- **P1 即时**：记录不等任何网络往返（本地写即生效）；
- **P2 无损合并**：任两份记录汇总后，不丢任何一条信息；
- **P3 可追溯**：任一条当前可见的值，能回答"谁、何时、根据什么记的"；
- **P4 可时间旅行**：任取过去时刻 t，能重建当时的认知状态；
- **P5 冲突可见**：两个记录员的矛盾记录不互相消灭，而是并存可查；
- **P6 简单表面**：使用者面对的是"一个对象 + 几个链接函数"。

Cairn 的结构选择：**内容寻址的原子断言流 + 读取时解析**。

---

## 1. 断言（Claim）—— 唯一的原子

一条断言是一个九元组：

```
claim = (subject, predicate, value, valid_from, valid_to,
         recorder, recorded_at, source, scope)
```

| 字段 | 语义 |
|---|---|
| subject | 主体节点 id（如 `person:ada`、`server:main`、`me`） |
| predicate | 谓词（属性名、关系名、事件名，如 `role`、`member_of`、`did`） |
| value | 值：标量、或另一节点 id（即链接）、或结构化 JSON |
| valid_from | **有效时间**起点：该事实在现实世界成立的时刻 |
| valid_to | 有效时间终点（None = 至今；断言可带闭区间） |
| recorder | 记录员 id（谁写的） |
| recorded_at | **记录时间**：写入设备的时刻（墙上时钟，仅作次级排序） |
| source | 出处：引文 URL、记录员备注、或 `ai:<model>` |
| scope | 可见域标签（`private` / `family` / `guild` / `company`） |

**万物皆断言**：

- 属性：`(person:ada, name, "Ada Lovelace", t1, None, mom, t1, "户口本", family)`
- 链接：`(person:ada, member_of, org:analytical, t2, None, mom, t2, "合影", family)`
  —— value 是另一节点 id，图边即谓词；
- 事件：`(player:steve, broke_block, "diamond", t3, None, bot, t3, "log#4471", guild)`
- 撤回：`(·, retract, <某断言 cid>, t4, None, gm, t4, "误录", guild)`
- 争议：`(·, dispute, <某断言 cid>, t5, None, auditor, t5, "来源失效", company)`

节点不是表里的行，**节点是其 subject 的全部断言的视图**（§4）。

---

## 2. 规范编码与内容寻址（核心机制一）

断言的**规范编码**：字段固定顺序、键排序、浮点格式化为 `repr 最短往返`、
字符串 NFC 归一、无多余空白的 JSON。两条语义相同的断言
（同九元组）无论由谁、在哪台设备、何时录入，规范编码逐字节相同。

断言 id：`cid = blake2b(canonical(claim), digest_size=16)` 的十六进制。

由此得到三个性质，全部由编码保证、由测试钉死：

- **寻址幂等（Idempotence）**：同一事实被录入两次 ⇒ 同一 cid ⇒ 流中是一条；
- **跨设备一致**：两设备记录同一事实 ⇒ cid 相同 ⇒ 合并后自然去重；
- **乱序无害**：流是集合而非序列，到达顺序不影响内容。

**这是与 FtM/Wikidata 的结构性分野**：它们的事实没有内容身份，
"同事实两次录入"是两条记录，去重是事后的管道步骤（nomenklatura 之类的
实体消歧）；Cairn 把去重下沉到存储层，由哈希免费获得。

---

## 3. 读取时解析（核心机制二）

流中永不覆盖。一个 subject 的同一 predicate 可能有多条断言
（不同 valid_from、不同 recorder、矛盾 value）。**当前值不是存出来的，
是读出来的**：

> **解析规则**：给定 (subject, predicate, as_of)，候选 = 流中匹配且
> `valid_from ≤ as_of < valid_to`（valid_to=None 视为 +∞）且未被撤回的断言。
> 当前值 = 候选中 `valid_from` 最大者；并列时 `recorded_at` 最大者；
> 再并列取 **cid 字典序最大**者。

三级 tie-break 全部确定，不依赖墙上时钟可信度（recorded_at 仅高于 cid
一级），因此：

- **确定性**：同一断言集合 ⇒ 同一解析结果，与录入顺序、设备、进程无关；
- **时间旅行**：as_of 取过去时刻即得当时认知（P4）；
- **历史免费**：旧值是流里的普通断言，查询"name 从 t1 到 t2 变过几次"
与查当前值同复杂度。

**冲突（P5）**：候选 ≥2 且 cid 决胜后仍有**同等资格的矛盾 value**
（即 valid_from 与 recorded_at 都相同、仅 cid 不同）⇒ 标记 `disputed`，
读取策略返回全部候选而非单一值。冲突不消失、不覆盖、可查询——
"这条有争议"是一等公民。

**撤回（tombstone）**：retract 断言引用目标 cid。读取时被撤回的断言退出
候选，但物理保留在流中（P2：合并时不会被"复活"——撤回本身也是断言，
两边都有则都在，边都不会单方面消失）。

---

## 4. 合并定理（核心机制三）

> **定理（合并即并集）**：设 A、B 为两份 Cairn 记录（断言的多集，按 cid
> 去重后为集合）。定义 merge(A, B) = A ∪ B（按 cid）。则 merge 满足
> 交换律、结合律、幂等律；且对任意 A, B，merge(A, B) 的读取时解析结果
> 只依赖于 A ∪ B 这一集合，与拆分方式、传输顺序、到达时机无关。

**证明概要**：解析规则（§3）是集合函数——候选集由集合成员性决定，
决胜链 (valid_from, recorded_at, cid) 是全序，与集合外任何顺序无关。
∎

**推论 1（无损）**：merge 不删除任何断言 ⇒ P2。
**推论 2（可证明收敛）**：任意拓扑的反复合并（夫妻两台设备经 U 盘中转、
企业三地经中继）收敛到同一集合 ⇒ 同一解析结果。
**推论 3（传输优化）**：差量同步 = 交换 cid 集合差（version vector 思想），
合并成本 O(|差|)。

**为什么不是 CRDT？** grow-only set CRDT 正是本模型的学术名字，
但 Cairn 不引入通用 CRDT 机制：领域原子（带 valid-time、出处、scope 的断言）
使 merge 退化为集合运算，无需为文本/序列等通用类型设计收敛规则。
可解释性（非程序员能听懂"两条记录并起来，谁的大听谁的，打平了都显示"）
是刻意选择。

---

## 5. 双时间模型（bitemporal）

- **有效时间**（valid_from/valid_to）：现实世界的真。`person:ada` 的
  `role=director` 于 2020-01-01 生效、2024-06-30 失效——这是两条断言还是一条？
  Cairn 用**一条带闭区间的断言**表达连续任期，用两条表达中断任期；
  "现任"查询 = valid_to=None 或 valid_to > now。
- **记录时间**（recorded_at）：系统的知。2024-07-02 才补录 2020 年的任命，
  valid_from=2020-01-01、recorded_at=2024-07-02——查询"我们在 7 月 1 日
  知道什么"用 recorded_at 过滤，"2020 年 3 月事实是什么"用 valid_from。
  两者独立，构成 bitemporal（继承 FtM 的 first/last-seen 思想，但下沉到逐断言）。

---

## 6. 可见域（scope）

每条断言带 scope 标签。**scope 是便利分层，不是安全边界**（如实记录：
企业版另有加密段作为真边界，见 BUSINESS.md）。读取时按记录员角色过滤：

- 个人单机：scope 全开，`private` 仅本人；
- 家庭：`family` 全家可见，各自 `private` 互不可见；
- 游戏服：`guild` 对全服管理可见，玩家只能看自己被记的；
- 企业：`company` 全员，`hr` 限角色，`private` 仅本人——RBAC 在企业版。

---

## 7. 存储与复杂度

- **存储**：目录即数据库。`segments/*.cseg` 为追加式规范 JSONL
  （一行一断言，按 cid 排序后紧凑）；索引（by-subject / by-predicate /
  by-value 倒排 / by-valid-time）为**派生物**，加载时重建，可随时删除。
  目录拷贝 = 备份；git 可 diff；`cairn verify` 全量重放校验。
- **复杂度**：写入 O(1) 追加；合并 O(|A∪B|) 去重（哈希集）；解析 O(候选)；
  当前值查询 O(log n + k)（subject×predicate 索引 + 候选决胜）；
  时间线 O(log n + 结果)；全文反查 O(倒排 posting 长度)。
  规模曲线由 bench/ 给出，不口头承诺。

## 8. 确定性承诺

同输入断言集合 ⇒ 同规范编码 ⇒ 同 cid ⇒ 同索引 ⇒ 同解析 ⇒ 同导出字节。
全链路无墙上时钟依赖（recorded_at 只参与 tie-break，不决定集合成员性），
无哈希序依赖（所有对外输出按 cid/键排序遍历）。
