# DesayMem_light 整体算法架构图

```mermaid
flowchart LR
    IN[车端消息<br/>tenant/user/vehicle/occupant/session]
    SB[SessionBuffer<br/>512 tokens]
    TS[LightMem Topic切分<br/>BGE-M3]
    F[L1 Fact<br/>Mem0 ADD]
    E[L2 Topic Event<br/>确定性组装]
    CE[Cross-Event<br/>异步低频总结]
    P[L3 Profile<br/>结构化项 + 文本快照]

    Q[用户 Query]
    R[九步混合检索<br/>身份/Tag/时间/向量/FTS]
    C[Agent Context<br/>不超过2000 tokens]

    PG[(PostgreSQL + pgvector<br/>业务事实源)]
    SQ[(SQLite<br/>历史兼容缓存)]
    OB[Outbox]
    JS[(逐表逐行 JSON Mirror)]

    IN --> SB --> TS --> F --> E
    E -.每10条或每日.-> CE --> P

    Q --> R --> C
    F --> R
    E --> R
    CE --> R
    P --> R

    SB --> PG
    F --> PG
    E --> PG
    CE --> PG
    P --> PG
    PG --> OB --> JS
    PG -.兼容投影.-> SQ --> OB

    MOD[可插拔模块<br/>Protocol + Registry + Versioned Config]
    MOD -.装配.-> TS
    MOD -.装配.-> F
    MOD -.装配.-> E
    MOD -.装配.-> CE
    MOD -.装配.-> P
    MOD -.装配.-> R
```

## 汇报口径

- 在线链路只在 Topic 封包后进行一次 L1 Qwen 抽取；Topic 切分、Event 生成和检索不调用 Qwen。
- Cross-Event 和 Profile 在异步低峰批量处理，各自每批最多一次 Qwen。
- PostgreSQL 是唯一业务事实源，SQLite 是兼容缓存，JSON 通过 Outbox 保证可恢复的一一对应。
- 所有算法节点均通过稳定接口、显式注册和版本化配置实现可插拔。
