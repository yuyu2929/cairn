# Cairn 调研报告 — 现有项目优缺点映射与空位分析

> 项目定位：**本地优先的共享记忆数据库**。一个人、一个家庭、一个游戏服务器、
> 一个企业，用来记录"谁、什么时候、做了什么、和什么相关"，并能高效反查。
> 上游诉求来自 open-source-ideas/ideas#429（Network DB：比维基百科简单的
> 人-地-事件映射工具）及其扩展场景（个人时间线 / 服务器档案 / 家庭共享 /
> 企业内信息传递）。

调研对象分四族：**维基/知识库**（DokuWiki、TiddlyWiki、MediaWiki）、
**本地优先知识管理**（Obsidian、Logseq、Anytype、Trilium）、
**权贵映射/调查数据模型**（LittleSis、FollowTheMoney/Aleph、Wikidata）、
**同步架构**（local-first 七原则、CRDT/Yjs/Automerge、append-only 事件溯源）。

---

## 1. 维基族：DokuWiki / TiddlyWiki / MediaWiki

| 项目 | 核心做法 | 优点 | 缺点（Cairn 要去掉的） |
|---|---|---|---|
| MediaWiki | 服务端 SQL + 每页修订历史 | 协作审核流成熟、权限体系完整 | 重（PHP+MySQL）、查询只有页面级、无结构化关系、时间线不是一等公民 |
| DokuWiki | 纯文件存储，无数据库 | 极简、可 git 化、自托管零依赖 | 数据即文本文件，"谁什么时候做了什么"查不了；无双向链接索引；无 schema |
| TiddlyWiki | 单文件 HTML，条目即微件 | 单文件分发、非线式组织 | 单用户心智；并发编辑即冲突文件；大规模后退化；无合并语义 |

**共性缺陷**：三者都是**文档中心**（page/tiddly 是主角），关系是附属文本链接，
时间是附属修订记录。Cairn 的场景（"某人什么时候干了什么"）需要**断言中心 +
时间一等公民**，这是结构性差异，不是功能差距。

## 2. 本地优先知识管理：Obsidian / Logseq / Anytype / Trilium

| 项目 | 核心做法 | 优点 | 缺点 |
|---|---|---|---|
| Obsidian | 本地 Markdown 文件夹 + 双向链接 + 插件生态（2000+） | 文件可读性/可移植性最强；搜索快；完全离线 | 不是数据库：无类型、无 schema、无查询语言；为个人设计，协作靠外挂（git/网盘）；关系靠人工维护的链接 |
| Logseq | 大纲 + 块级双链，本地优先 | 日记/时间线体验好 | 同样无结构化查询；数据格式随版本演进有迁移风险 |
| Anytype | 对象/类型/关系模型 + 本地优先 + E2EE P2P 同步（IPFS） | 最接近 Cairn 的对象图野心；隐私架构好 | 对象模型要求用户 upfront 想清楚结构（违背 issue#429 的"简单"诉求）；IPFS 同步慢且不可控；beta 期稳定性；无 bitemporal 语义（改属性即覆盖，历史不可查）；无逐值出处 |
| Trilium | 树状笔记 + 脚本扩展 + 应用数据库 | 结构可塑 | 层级目录心智；同步非头等公民 |

**关键结论**：Anytype 证明了"本地优先对象图"有真实需求，但它的三个弱点
恰好是 Cairn 核心要解决的：** upfront 结构化负担**（Cairn：无 schema，
任何断言随时可记）、**属性覆盖丢历史**（Cairn：断言流 + 读取时解析）、
**同步黑箱**（Cairn：合并 = 可证明的集合并集）。

## 3. 权贵映射/调查数据模型：LittleSis / FollowTheMoney / Wikidata

这是与本项目场景最亲的一族，也是空位分析的重点。

| 项目 | 核心做法 | 优点 | 缺点 |
|---|---|---|---|
| LittleSis | Rails + MySQL，实体+关系+引文，公开 API | "权贵关系图谱"场景的开创者；引文随每条关系 | 单一服务器真相；编辑即覆盖；无时间有效期模型（任期变动丢历史）；无本地优先/离线；合并两个库靠人工 |
| FollowTheMoney (OCCRP) | 实体(Entity)+引用(References)+流(Streams JSONL)； interstitial entity 表示带时间的关系（Ownership: startDate/percentage）；StatementEntity 逐值带出处 | **interstitial entity 正是"带时间的关系"的正解**；ValueEntity/StatementEntity 双表示；流式 JSONL 可超内存排序聚合 | 数据模型是**管道格式**不是存储引擎；出处挂在 dataset 级（first/last seen），非逐断言；无内容寻址（同事实两次录入 = 两条）；聚合是线下 pipeline 步骤，不是在线查询；无冲突表示（冲突在清洗阶段被人为消灭） |
| Wikidata | 中心服务端，statement(claim)+reference+rank 模型 | statement 级出处与 rank（preferred/normal/deprecated）是世界级设计 | SPARQL 学习墙；重；单点真相；无本地优先；rank 是人工维，不是自动时间解析 |

**FtM 的 interstitial entity 与 Wikidata 的 statement+reference 是两大先例智慧**，
Cairn 的核心设计直接继承两者（关系即实体、逐断言出处），但空缺在于：
**没有一个项目把"记录"做成内容寻址的原子断言流，使合并成为纯集合并集、
历史成为免费副产品、冲突成为可表示的一等对象**。这正是空位。

## 4. 同步架构：local-first 七原则 / CRDT / 事件溯源

**七原则**（Ink & Switch / Kleppmann et al.）：快（不等网络）、多设备、离线、
协作（收敛不覆盖）、长寿（厂商倒了数据还在）、安全（用户持钥）、可控
（可检查可导出可删除）。Cairn 的验收标准直接按这七条写。

**CRDT 谱系与 Cairn 的取舍**：

| 技术 | 保证 | 对 Cairn 的适配 |
|---|---|---|
| Yjs/Automerge | 通用文本/对象收敛 | 过度通用：不带领域语义（出处、有效期、可见域），合并规则不可解释给非程序员 |
| LWW-register | (timestamp, actor) 大者胜 | 简单但丢数据——"谁什么时候改的"全没了；违背家庭/企业场景的可追溯诉求 |
| append-only 事件溯源 | 事件即真相，状态=重放 | **Cairn 选这条**：事件=断言，内容寻址保证重放/合并幂等 |
| version vector / delta sync | 只传缺失增量 | 作为传输层优化采纳（合并时按 cid 集合差） |
| tombstone | 删除不复活 | 采纳：撤回是流内一条断言，永不物理删除 |

## 5. 空位结论：Cairn 的核心主张

综合四族，现有项目没有一个同时做到：

1. **断言中心**：属性、关系、事件、撤回全是同一种原子（subject, predicate,
   value, valid-time, recorder, source, confidence, scope）——issue#429 作者要的
   "一个类+几个链接函数"只是这之上的糖；
2. **内容寻址**：断言 id = 其规范编码的哈希 ⇒ 同事实重录幂等、跨设备合并=并集、
   乱序到达无害；
3. **读取时解析**：当前值 = valid-time 最大的断言；历史永不覆盖 ⇒ 时间旅行查询
   免费获得；
4. **冲突可表示**：两个记录员记下矛盾事实，两条断言并存 + 标记 disputed，
   消灭冲突是清洗 pipeline 的人工步骤（FtM），不是存储的职责；
5. **逐断言可见域**：一条断言带 scope（private/family/guild/company），
   家庭私事与企业公示在同一库分层可见——这是"一个库服务四个场景"的关键；
6. **零依赖单机优先**：纯标准库，单目录即数据库，目录拷贝=备份，git diff=审计。

竞争差异化一句话：**Anytype 有对象图但没有带时间的断言流与可证明合并；
FtM 有逐值出处但只是在管道里；Wikidata 有 statement 模型但是在云端；
Obsidian 有本地文件但没有结构。Cairn 是四者的交集最小实现。**

## 6. 风险与对策（如实记录）

- **查询性能**：全内存索引在百万断言规模会痛。对策：索引按需构建 +
  段（segment）级惰性加载；基准给出规模曲线而非口头承诺。
- **合并的理论边界**：并集合并只在"断言不可变"下成立。对策：撤回/争议
  也是断言（流内新增，不删旧），不变式由测试钉死。
- **可见域不是安全边界**：scope 是便利分层，不是加密隔离。企业版另有
  加密段（ChaCha20-Poly1305，纯 Python）作为真边界，如实写进 BUSINESS.md。
- **AI 灌入的幻觉**：AI 断言一律以 unverified + source=ai:<model> 落库，
  查询默认过滤，审核通过才进 verified——验证门是 ingest 层的一等功能。
