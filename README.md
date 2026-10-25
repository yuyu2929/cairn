# Cairn — local-first shared memory

[![CI](https://github.com/yuyu2929/cairn/actions/workflows/ci.yml/badge.svg)](https://github.com/yuyu2929/cairn/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![deps](https://img.shields.io/badge/runtime%20deps-none-34d399)](pyproject.toml)

> 一个数据库，四种记忆：**个人维基**（我什么时候做过什么）、**游戏服维基**（这个玩家干过什么）、**家庭互通**（两台设备合成一个真相）、**企业传递**（员工信息随许可证加密流转）。

Cairn 是一条简单的规则：**一切皆 Claim（断言）**。数据库是一个目录，合并是集合并，冲突被保留而非悄悄解决，历史永不被覆盖。没有服务器，没有网络，没有依赖——只有标准库。

```
pip 不需要。cairn 是纯 Python 标准库实现的。
```

---

## 为什么是它

- **个人 / 家庭 / 服务器都愿意用**：一个 2 MB 的目录就是一整个数据库。复制目录就是备份，复制目录就是同步，发给别人就是分享。
- **企业心甘情愿付费**：免费版已经能跑所有事；付费买的是**加密范围（encrypted scopes）**、**席位（seats）**、**审计流（audit log）**——以及那份“不能伪造的许可证”。代码全开源，付费买的是*使用权*，不是黑盒。
- **数学上诚实**：合并是交换/结合/幂等的（grow-only set CRDT）；加密是 RFC 8439 / RFC 8032 / RFC 5869 逐字节验证过的；位时间（valid-time）与记录时间（recorded_at）分开，过去可复现。

---

## 30 秒上手

### 作为库

```python
import cairn
g = cairn.open("~/my-wiki", create=True)

# 记录一个事实（谁在何时断言了什么）
g.record("alice", "role", "admin", "2026-01-01", recorder="alice")

# 读取当前值（读时决议：取 valid_from / recorded_at / cid 最大者）
print(g.value("alice", "role"))          # -> 'admin'

# 位时间查询：2020 年的“我”看到什么？（即使日志里已有更晚的事实）
g.view().at_time("2020-06-01")

# 撤回一条断言——历史不丢，只是不再“生效”
c = g.view().resolve("alice", "role").candidates[0]
g.retract(c.cid, "alice", "写错了")
```

### 作为命令行

```bash
python -m cairn.cli init ~/my-wiki
python -m cairn.cli record ~/my-wiki alice role admin --valid-from 2026-01-01 --recorder alice
python -m cairn.cli get    ~/my-wiki alice role
python -m cairn.cli search ~/my-wiki 三月          # 中文也能逐字搜
python -m cairn.cli history ~/my-wiki alice        # 完整位时间线（含撤回/争议/背书标记）
python -m cairn.cli verify  ~/my-wiki              # 完整性检查
python -m cairn.cli serve  ~/my-wiki --port 8777   # 本地 Web UI
```

`python -m cairn.cli --help` 列出全部子命令：`init record get history search nodes links traverse retract dispute endorse alias merge export import compact verify stats serve ingest enterprise`。

---

## 四个场景，同一个引擎

| 场景 | 关键能力 | 演示 |
|------|----------|------|
| 个人维基 | 位时间线、撤回、过去可复现 | `examples/demo.py` 场景 1 |
| 游戏服维基 | 机器人批量 ingest（带校验闸）、身份别名合并、争议/背书 | 场景 2 |
| 家庭互通 | 两台设备各自写、导出 bundle、合并成共享真相、范围可见性 | 场景 3 |
| 企业传递 | 许可证门禁、范围加密（ChaCha20-Poly1305）、审计流、席位 | 场景 4 |

跑完整演示：

```bash
python examples/demo.py
```

---

## 模型（一句话版）

一条 Claim 是九元组：`(subject, predicate, value, valid_from, valid_to, recorder, recorded_at, source, scope)`，内容寻址：

```
cid = blake2b(canonical_bytes(claim), digest_size=16).hexdigest()   # 32 位十六进制
```

- **读时决议**：`(subject, predicate)` 的当前值 = 未被撤回的候选中 `(valid_from, recorded_at, cid)` 最大者。
- **合并定理**：`merge(A, B) = A ∪ B` —— 交换、结合、幂等。所以“把段文件拷到 U 盘”就是完整的同步原语。
- **冲突是一等公民**：相同 rank 的不同取值被标记 `disputed` 并保留，而不是让时间戳悄悄选边。
- **位时间**：`valid_from`（事实成立的真实时间）与 `recorded_at`（系统知晓的时间）分离。
- **范围阶梯**（便利层，非安全边界）：`private < family < guild < company < public`。

完整论证见 [`docs/THEORY.md`](docs/THEORY.md)；取舍与风险见 [`docs/RESEARCH.md`](docs/RESEARCH.md)。

---

## 性能（真实数字，非宣传）

在普通机器上（`python examples/bench.py`，数量级参考）：

| 操作 | 吞吐 |
|------|------|
| 单条记录 | ~37,000 ops/s |
| 批量 ingest（带校验闸） | ~30,000 ops/s |
| 读时决议（2 万 claim 上） | ~36,000 ops/s |
| 合并两个 1 万 claim 库 | ~18,000 ops/s |
| 全文搜索（2 万文档，CJK 逐字 AND） | 返回前 50 条 ~10 ms/查询 |

写入是追加式 JSONL，读取是打开时从日志重建的内存索引。**没有服务器，没有网络。** 全部 70 个测试通过；加密原语对照 pycryptodome / RFC 测试向量逐字节一致。

---

## 企业版（open-core，诚实说明）

免费社区版包含**一切功能**——CRDT 合并、位时间、冲突保留、范围可见性、CLI、Web UI、机器人 ingest。

企业版额外提供：

- **许可证门禁**：Ed25519 签名的许可证文件。没有供应商公钥验证通过的许可证，加密/审计等能力就是惰性的。
- **加密范围**：`company` 等范围内的 claim 在静态时是密文（ChaCha20-Poly1305），密钥由许可证主密钥按库/HKDF 派生——泄露段文件没有许可证就是噪声。
- **审计流**：每次企业写入同时落一条审计 claim，撤回也抹不掉“谁在何时断言了什么”。

诚实的局限（详见 RESEARCH.md）：许可证文件必须保密才谈得上加密；离线过期检查用本机时钟；这是 open-core 不是 DRM——拿到源码的客户可以绕过门禁，但他们**无法伪造许可证**。

生成供应商密钥与许可证：

```bash
python -m cairn.cli enterprise keygen --out vendor.json
python -m cairn.cli enterprise license --vendor-sk vendor.json --org acme --seats 50 --out license.json
python -m cairn.cli enterprise encrypt --db HR --license license.json --vendor-pk vendor.pub
```

---

## 正确性

- **74 个测试**全部通过：`tests/`（claim / store / graph / merge / scope / ingest / enterprise / crypto / 合并不变量）。
- **加密逐字节验证**：ChaCha20-Poly1305（RFC 8439）、Ed25519（RFC 8032）、HKDF-SHA256（RFC 5869）对官方测试向量与 pycryptodome 交叉核对一致。
- `store.verify()` 全量回放校验：重算每条 cid、检查段命名顺序，能报告截断的末行（崩溃中追加）而非默默信任。

跑测试：

```bash
python -m unittest discover -s tests     # 或: pytest -q
```

---

## 状态

当前版本 1.0.1，变更见 [CHANGELOG.md](CHANGELOG.md)。核心库 11 个模块、约 3000 行、零第三方依赖；CLI、Web UI、企业加密、机器人 ingest 均已实现并通过测试。

设计哲学一句话：**日志是唯一真相，其它一切（索引、视图、合并结果）都能从日志重建**（`docs/THEORY.md` 第 7 节）。

---

## 许可证

见 [`LICENSE`](LICENSE)（MIT）。
