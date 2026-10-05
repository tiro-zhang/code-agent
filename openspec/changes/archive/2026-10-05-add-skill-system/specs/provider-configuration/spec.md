## ADDED Requirements

### Requirement: 独立 Skill 的模型预算配置
主配置 SHALL 支持可选 skill_models JSON 对象，以当前服务下模型 ID 为键、显式 context_window 和可选 max_output_tokens 为值。映射及每项 SHALL 拒绝重复键、未知字段、非对象值、非法模型标识、非正整数和布尔预算；输出额度省略 SHALL 沿用主配置，每个窗口 SHALL 满足 context_window > max_output_tokens + 13000。省略映射 SHALL 不改变旧配置启动行为。独立 Skill 省略 model 或指定主模型 SHALL 使用主模型预算；指定其他模型 SHALL 使用映射中的对应预算并复用主 protocol、base_url、api_key 和 thinking，SHALL NOT 从 Skill 接受凭据、任意配置路径或跨服务地址。缺少对应预算 SHALL 在子请求前返回 skill_model_unavailable，不猜窗口或静默换回主模型；服务拒绝选定模型或参数 SHALL 如实返回运行失败。共享 Skill 指定 model SHALL 按 Skill 文件错误处理，不改变主模型及续接历史。

#### Scenario: 旧配置没有模型映射
- **WHEN** 主配置合法且省略 skill_models
- **THEN** 旧启动行为保持，未指定独立模型的 Skill 沿用主模型配置

#### Scenario: 独立模型有显式预算
- **WHEN** Skill 指定已配置的其他模型 ID
- **THEN** 子模型请求使用该 ID、其窗口及输出上限和主服务连接，主模型、主历史及主估算锚点保持

#### Scenario: 模型预算缺失
- **WHEN** 独立 Skill 指定其他模型，但主映射没有该项
- **THEN** 返回 skill_model_unavailable，不发送子请求、不使用主模型代替

#### Scenario: 非法备用窗口
- **WHEN** 映射项为布尔窗口、含重复键或窗口不大于输出加余量
- **THEN** 启动配置校验失败，不创建模型客户端或开始 Skill 执行
