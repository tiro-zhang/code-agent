# tool-permissions Specification

## Purpose

定义 MewCode 在本地工具实际访问文件或执行命令之前如何决定是否获准，覆盖不可解除的安全边界、三来源规则、会话信任模式及可记忆的人工授权，并明确拒绝后继续任务的行为与 shell 检查的能力边界。

## Requirements

### Requirement: 执行前统一权限判定

系统 SHALL 对每次合法工具调用在实际操作前执行权限判定，不依赖模型提示词或模型是否主动请求批准。硬限制优先于规则、权限模式和授权记录；规则先合并再应用权限模式，仅在结果需要授权时检查有效批准记录或询问用户。文件操作 SHALL 使用真实目标路径；shell SHALL 使用完整原始命令及其可解析的显式子命令。搜索 SHALL 对各候选文件应用同一判定语义。MCP 工具 SHALL 在外部执行请求发出前按稳定工具别名与完整有效参数执行相同的规则合并、strict/default/bypass 模式及人工授权判定；其精确授权范围 SHALL 使用真实项目根、Server 名及有效配置指纹、原始工具名和规范 JSON 参数。MCP 连接与工具发现本身 SHALL NOT 被当成工具执行批准。本地路径边界和 shell 文本黑名单 SHALL 继续保护内置工具，SHALL NOT 被描述为检查或隔离外部 Server 的内部行为；规划模式禁止 MCP 工具的限制 SHALL 始终适用。

#### Scenario: 放行依据不能解除硬限制
- **WHEN** 调用同时具备 allow 规则、永久授权或放行模式，但命中内置黑名单、路径越界或规划模式禁用工具
- **THEN** 调用仍被拒绝且不产生该操作的副作用，系统不提供绕过该限制的批准选项

#### Scenario: 工具声明之外仍检查调用
- **WHEN** 模型构造一个被规则禁止但已注册的工具调用
- **THEN** 系统在实际操作前拒绝并返回结构化结果，不依赖模型自行遵守权限说明

#### Scenario: 外部调用采用相同权限优先级
- **WHEN** MCP 工具调用具有旧授权但当前规则命中 deny，或调用在规划模式被禁止
- **THEN** 系统在发送外部执行请求前拒绝，不以 strict/default/bypass、旧授权或 Server 的只读提示绕过拒绝条件

#### Scenario: 发现工具不等于批准执行
- **WHEN** 系统已连接 MCP Server 并成功发现工具，但 default 模式下某次调用没有规则命中或有效授权
- **THEN** 该调用仍需要人工授权，未决定前不发送工具执行请求

### Requirement: 不可配置解除的危险命令黑名单

系统 SHALL 先对完整原始 shell 命令文本执行内置正则黑名单检查，命中即返回黑名单拒绝；未命中时再解析结构，并对可解析的显式子命令及其静态表示再次执行黑名单检查。黑名单 SHALL 至少覆盖可见的根目录或用户主目录递归删除、磁盘格式化或擦除、向原始磁盘设备写入以及已知 fork bomb 形式；各模式 SHALL 有稳定标识与中文拒绝原因。三份 YAML、授权记录和所有权限模式 SHALL NOT 删除、覆盖或绕过该集合。该层 SHALL 被说明为已知危险文本模式拦截，不得宣称可以识别脚本内部或运行时生成的全部危险行为。

#### Scenario: 放行模式仍拦截已知危险命令
- **WHEN** 放行模式下命令文本命中一个内置黑名单模式
- **THEN** 系统返回 permission_denied，指出黑名单标识，不启动 shell 且不询问是否继续

#### Scenario: 配置尝试禁用黑名单
- **WHEN** 权限配置包含禁用或覆盖内置黑名单的字段
- **THEN** 配置被视为无效，而不是改变黑名单或静默忽略该字段

### Requirement: 三来源规则无覆盖顺序合并

系统 SHALL 从用户级 `~/.mewcode/permissions.yaml`、项目级 `<root>/.mewcode/permissions.yaml` 和本地级 `<root>/.mewcode/permissions.local.yaml` 读取 rules，其中 root 是启动时固定并解析后的工作根目录，与供应商 .env 所在目录无关。所有命中规则 SHALL 合并为一个集合，按 deny 高于 ask、高于 allow 决定结果；规则来源、文件读取顺序、列表顺序及 exact 比 glob 更具体都 SHALL NOT 改变该优先级。没有任何命中 SHALL 保留为未命中状态，随后由权限模式处理。文件不存在 SHALL 等同于该来源没有规则。

#### Scenario: 三来源互相冲突
- **WHEN** 用户级存在匹配 allow、项目级存在匹配 deny、本地级存在匹配 ask
- **THEN** 结果为 deny，调换三个文件的内容或加载顺序后结果不变

#### Scenario: 精确放行不能盖过通配询问
- **WHEN** 同一目标同时命中精确 allow 和 glob ask，且没有 deny
- **THEN** 合并结果为 ask，而不是因为精确规则更具体就放行

#### Scenario: 项目根独立于供应商配置
- **WHEN** 用户在项目 A 启动会话，但 --config 指向项目 B 下的供应商配置
- **THEN** 项目级和本地级权限文件仍从 A 读取

### Requirement: 明确的规则格式与工具匹配对象

每条规则 SHALL 包含 effect、rule 和 match；effect 仅接受 allow、ask、deny，match 仅接受 exact、glob，rule 使用 `工具名(模式)`。系统 SHALL 将 Bash 规范化为 execute_command 的配置别名，保留现有六个模型 API 工具名，并接受符合 mcp-tools 命名契约的稳定 MCP 工具别名。既非内置名称又非语法合法 MCP 别名的未知工具名、未知字段、非法表达式或缺少匹配方式 SHALL 被报告为配置错误。语法合法但尚未发现或 Server 离线的 MCP 别名 SHALL 允许保存在规则中，SHALL NOT 仅因当前未注册而使权限配置无效；实际调用 SHALL 仍要求名称已注册。exact SHALL 按整个匹配对象区分大小写比较，glob SHALL 匹配整个对象而不是任意子串。

文件与搜索工具的匹配对象 SHALL 是解析真实路径后的项目相对路径，统一使用 `/` 分隔；路径 glob 的 `*`、`?`、字符集合仅在一个路径片段内匹配，`**` SHALL 表示零个或多个路径片段。search_code 的内容正则 SHALL NOT 被当成权限路径。Bash 的 glob SHALL 使用字符串 glob 语义，其 allow 适用范围另受 shell 结构限制。规则 SHALL 仅作用于指定工具，不自动扩展到具有相似用途的其他工具。MCP 规则的工具名 SHALL 使用稳定工具别名，匹配对象 SHALL 是完整有效参数对象的规范 JSON 字符串；exact SHALL 比较整个规范字符串，glob SHALL 使用匹配整个字符串的字符串 glob 语义，SHALL NOT 使用文件路径 glob 语义或对单个参数另行拆分放行。MCP 规则 SHALL NOT 用原始工具名跨 Server 匹配。规范 JSON SHALL 递归按 Unicode 码点排序对象键、移除无意义空白、保留非 ASCII 字符、数组顺序及 JSON 值类型，并拒绝非有限数值；同一规范 SHALL 用于规则匹配及批准记录，SHALL NOT 使用配置凭据或连接参数替代工具调用参数。

#### Scenario: 命令里的字面通配符
- **WHEN** exact 规则的模式包含字面 `*`
- **THEN** 该字符按字面匹配，不因其出现而把规则推断成 glob

#### Scenario: 符号链接别名命中真实目标规则
- **WHEN** 文件工具通过项目内链接访问 src/private.txt，真实目标命中 deny
- **THEN** 系统按真实目标拒绝，不能借助链接路径名称绕过规则

#### Scenario: 路径片段与递归匹配
- **WHEN** 路径模式分别为 src/*.py 和 src/**/*.py
- **THEN** 前者不匹配 src/pkg/a.py，后者匹配 src/a.py 与 src/pkg/a.py

#### Scenario: 不同读取工具有独立规则
- **WHEN** 只有 read_file(secrets/**) 的 deny，search_code 没有对应命中规则
- **THEN** search_code 按自身规则及权限模式判定，不声称 read_file 规则禁止了所有读取通道

#### Scenario: 对象字段顺序不改变规则匹配
- **WHEN** 同一 MCP 工具的两次参数仅在对象键的输入顺序或 JSON 空白上不同
- **THEN** 两次调用使用相同规范 JSON 匹配对象，命中同一 exact 规则；数组顺序或实际参数值变化仍重新匹配

#### Scenario: Server 离线时加载规则
- **WHEN** 权限文件包含语法合法的稳定 MCP 别名规则，但本次启动未成功发现该工具
- **THEN** 权限配置仍可加载，内置工具不被阻止；模型调用未注册的该别名时返回 unknown_tool，不仅凭规则存在就执行

#### Scenario: 同名原始工具不共享规则
- **WHEN** 两个 Server 均提供名为 search 的原始工具，只有其中一个稳定别名配置 allow
- **THEN** 该规则仅匹配指定别名，另一个 Server 的工具按自身规则与模式判断

### Requirement: 三档权限模式

系统 SHALL 提供 strict、default、bypass 三种会话权限模式，默认值为 default。三档均 SHALL 保留硬限制和合并结果中的 deny；strict SHALL 将其他所有结果转换为需要授权，default SHALL 保留 allow / ask 并将未命中转换为需要授权，bypass SHALL 将 ask、allow 和未命中转换为放行。需要授权 SHALL 可以被有效批准记录满足，不等同于每次都必须重新询问。权限模式 SHALL 与 plan / execute 任务模式独立，切换其中一个维度不得隐式改变另一个。

#### Scenario: 完整模式矩阵
- **WHEN** 对 deny、ask、allow、未命中四种规则结果分别应用 strict、default、bypass
- **THEN** strict 得到拒绝、需授权、需授权、需授权；default 得到拒绝、需授权、放行、需授权；bypass 得到拒绝、放行、放行、放行

#### Scenario: 严格模式复用用户授权
- **WHEN** strict 模式下调用通过硬限制且无 deny，并命中仍有效的会话授权
- **THEN** 系统接受已有授权，不重复询问，也不因此改写规则结果

### Requirement: shell 规则检查完整结构

系统 SHALL 在执行前解析所支持的 POSIX shell 结构，deny / ask SHALL 同时检查完整原始字符串、显式简单子命令的文本，以及能在不运行展开的条件下获得的静态命令表示。任一部分命中 deny SHALL 拒绝整个调用；任一部分命中 ask SHALL 使该调用的规则合并结果至少为 ask。对子命令的 allow SHALL NOT 授予整个组合命令权限。

Bash glob allow SHALL 仅适用于能明确识别命令和静态参数的单个简单命令；包含命令连接、管道、重定向、子 shell、命令替换或无法静态确定命令／参数的调用 SHALL NOT 通过该类 glob allow 获准。完整命令的 exact allow SHALL 可以参与正常规则合并，仍受 deny、ask、权限模式及硬限制约束。无法解析或不受支持的语法 SHALL 返回 permission_check_failed 且不启动 shell，即使处于 bypass 或持有旧授权也不得执行。

#### Scenario: 简单命令与组合命令
- **WHEN** 唯一规则是 allow 的 Bash(git *)，分别调用 git status 和 git status && python script.py
- **THEN** 前者可以命中该 allow，后者不能凭该规则放行，在 default 下需要其他有效放行依据或用户授权

#### Scenario: 子命令拒绝阻止前缀执行
- **WHEN** Bash(git push*) 为 deny，调用 git status && git push
- **THEN** 整条调用返回拒绝，连 git status 也不先执行

#### Scenario: 精确组合放行仍服从子命令询问
- **WHEN** 组合命令整体命中 exact allow，其中一个显式子命令命中 ask
- **THEN** 合并结果为 ask，再应用权限模式和有效授权，而不是让整体 allow 覆盖 ask

#### Scenario: 引号和静态引用不隐藏命令
- **WHEN** 拒绝规则匹配 git push，而命令通过静态引号写成 git 'push'
- **THEN** 静态命令表示仍触发相应拒绝；系统不执行变量展开来进行检查

#### Scenario: 不支持的结构
- **WHEN** shell 文本无效或使用解析器无法可靠识别的结构
- **THEN** 返回检查失败及可改用简单命令的提示，不尝试执行以验证语法，也不终止 Agent Loop

### Requirement: 本次会话与永久授权独立于规则

系统 SHALL 为需要授权的操作提供拒绝、本次、本会话、永久四种决定。批准记录 SHALL 独立于 rules，不通过追加一个低优先级 allow 来表达用户批准。本次授权 SHALL 绑定当前调用标识、完整有效参数和已展示的目标集合，只能消费一次；参数或目标集合改变 SHALL 重新判定。

内置工具的会话与永久授权 SHALL 绑定真实项目根、规范工具名以及明确展示的目标范围：Bash 为完整精确 command；文件工具为该工具与真实目标路径；搜索工具为该工具与每个已批准的真实候选文件路径。文件和搜索的存续授权 SHALL 明确表示未来对这些路径使用同一工具时可以复用，不绑定当次文件内容或搜索表达式；不得把用户批准的路径自动扩大成目录 glob。会话授权 SHALL 跨任务、历史裁剪和 plan / execute 切换保留，退出失效；永久授权 SHALL 在同一真实项目根重启后恢复。不同工具、不同项目、不同命令或解析到另一个真实文件的路径 SHALL NOT 复用原记录。MCP 本次、会话与永久授权 SHALL 绑定真实项目根、Server 名及 mcp-configuration 定义的有效配置指纹、原始工具名和完整有效参数的规范 JSON，并保留稳定工具别名以供识别；本次授权还 SHALL 绑定当前调用标识且只能消费一次。MCP 存续授权 SHALL 仅复用完全相同的外部操作范围，SHALL NOT 扩大为该 Server 的全部工具、相似参数、参数子集或 glob。Server 有效配置身份或任一绑定项改变 SHALL 使旧批准不能覆盖新调用；批准展示 SHALL 明确上述范围，SHALL NOT 用未经用户审阅的秘密配置值作为展示内容。

#### Scenario: 显式 ask 被已有批准满足
- **WHEN** Bash(git *) 为 ask，用户对 git status 选择永久授权后再次运行相同命令
- **THEN** 不重复询问；git push 不复用这条授权

#### Scenario: 文件授权不跨工具扩张
- **WHEN** 用户对 read_file 的 src/a.py 选择会话授权，随后请求 edit_file 同一路径
- **THEN** 编辑按 edit_file 的规则重新判定，不能复用读取授权

#### Scenario: 搜索发现新文件
- **WHEN** 之前只批准 search_code 访问 src/a.py，后续同一搜索表达式新增候选 src/b.py
- **THEN** src/b.py 单独按当前规则和授权判定，不因查询字符串相同而自动获准

#### Scenario: 本次批准期间参数变化
- **WHEN** 用户批准后，待执行调用的内容、命令或已展示目标集合发生变化
- **THEN** 原本次批准不得用于改变后的操作，重新发起权限判定

#### Scenario: 相同外部调用复用授权
- **WHEN** 用户批准某 MCP 工具调用的会话授权后，在同一项目、同一 Server 有效配置和同一原始工具下再次提交规范 JSON 相同的参数，且当前规则无 deny
- **THEN** 该调用可使用原会话授权；仅对象键顺序或空白变化不触发重新批准

#### Scenario: 改变外部操作范围
- **WHEN** MCP 调用已有永久授权，后续 Server 的 command、args、cwd、URL、env 或 headers 等有效配置身份改变，或原始工具名、项目根、有效参数改变
- **THEN** 原批准不覆盖新调用；需要 ask 时重新取得明确批准，不把稳定别名仍相同当成授权仍有效

### Requirement: 授权持久化与撤销

永久批准 SHALL 默认写入本地级 permissions.local.yaml 的独立 approvals 集合，保留原 rules 和其他有效批准，不改写用户级或项目级规则。批准记录 SHALL 包含唯一标识、真实项目根、工具与精确授权范围；MCP 记录还 SHALL 包含 Server 名、有效配置指纹、原始工具名与规范 JSON 参数，SHALL NOT 将展开后的连接配置秘密写入批准记录；本章 SHALL 只从本地级读取永久批准，用户级和项目级用于规则。持久化 SHALL 原子提交并识别并发变化，不得覆盖其他刚写入的规则或授权；失败时 SHALL 明确告知永久保存失败，当前已明确批准的操作仅按本次授权处理，不冒充持久化成功。

用户 SHALL 能通过可信会话控制入口撤销当前项目全部会话授权或全部永久授权，不影响 rules。撤销与新增 deny SHALL 在下一次实际操作前生效；新建 ask 规则本身 SHALL NOT 隐式撤销用户先前的有效批准。语法合法且结构完整的 MCP 批准记录 SHALL 允许在工具尚未注册或 Server 离线时保留与加载，SHALL NOT 因无法连接而删除或放宽；实际使用时 SHALL 重新核对注册信息、当前有效配置指纹、完整参数及拒绝条件。当前项目的会话或永久授权撤销 SHALL 同时覆盖内置工具与 MCP 工具，对其他项目及 rules 保持不变。直接使用文件写入／编辑工具修改权限 YAML、其授权记录或这些路径的符号链接别名 SHALL 被拒绝；可信的授权与撤销入口 SHALL 能管理所需文件。此保护 SHALL NOT 被表述为能够隔离任意 shell 进程的文件访问。

#### Scenario: 永久批准不能压过后来添加的 deny
- **WHEN** 命令已有永久批准，后续任一规则来源新增匹配 deny
- **THEN** 下一次执行前返回拒绝，不因旧批准而继续执行

#### Scenario: 撤销后重新询问
- **WHEN** 用户撤销当前项目的永久授权，再请求只有该授权才能满足 ask 的操作
- **THEN** 系统重新询问，原 rules 保持不变

#### Scenario: 永久文件写入失败
- **WHEN** 用户明确选择永久批准，但保存因权限或并发冲突失败
- **THEN** 终端说明未能记住授权，当前操作仅按本次批准继续；重启或再次调用不能假装已有永久批准

#### Scenario: 模型尝试改写批准记录
- **WHEN** 模型调用 write_file 或 edit_file 改写受管理的权限配置或授权文件，即使匹配 allow 或处于 bypass
- **THEN** 文件工具拒绝该操作，模型文本或工具参数不得被当作用户授权决定

#### Scenario: 离线保留外部永久批准
- **WHEN** 本地权限文件有合法 MCP 永久批准记录，但本次启动对应 Server 离线或未发现工具
- **THEN** 系统保留并加载该记录，其他权限配置仍有效；工具调用只有在已注册且全部绑定项匹配时才可能使用该记录

#### Scenario: 撤销同时覆盖外部工具
- **WHEN** 用户通过可信入口撤销当前项目全部永久授权
- **THEN** 当前项目的内置和 MCP 永久批准都被清除，rules 与其他项目授权保持不变，保存失败时不报告撤销成功

### Requirement: 配置错误与执行前重新检查

权限 YAML SHALL 按数据配置解析，不得构造任意语言对象；重复键、未知字段、非法规则、非法批准记录和不支持的格式版本 SHALL 报错而非静默放宽权限。启动时配置无效 SHALL 阻止进入可执行会话并显示来源与原因；运行中发现配置无效 SHALL 使当前工具返回 permission_config_error，不回退为空规则、不启用 bypass、不终止整个会话。每次实际操作前 SHALL 使用最新有效配置检查拒绝条件；批准等待结束后 SHALL 再检查配置和目标真实路径，相关授权条件改变时重新判定。

#### Scenario: 运行中损坏配置
- **WHEN** 已启动会话中的权限 YAML 被改成无效格式
- **THEN** 后续工具返回权限配置错误而不执行，模型获得该错误；文件修复后后续调用能够重新判定

#### Scenario: 用户批准期间新增拒绝
- **WHEN** 一个调用等待批准时，用户在任一来源加入匹配 deny，随后提交批准
- **THEN** 执行前重新读取规则并拒绝，批准不能使用过期策略放行

#### Scenario: 批准期间替换链接目标
- **WHEN** 等待批准期间路径解析结果改变或移到项目外
- **THEN** 不沿用原批准访问新目标；项目外目标直接拒绝，项目内新目标重新判定

### Requirement: 拒绝可供模型调整且不伪装为取消

对于作用于整次调用的拒绝，黑名单、路径限制、规则 deny、用户拒绝或缺少可用授权通道 SHALL 生成可序列化的失败工具结果，包含稳定错误码、中文原因及适用的 not_started 标记；策略拒绝 SHALL 使用 permission_denied，保留既有路径／规划模式错误码的既有语义。拒绝 SHALL NOT 设置整轮取消标记，不取消同批其他已获准工具；模型 SHALL 在剩余请求预算内收到结果并有机会调整。结果 SHALL NOT 建议模型换一种写法绕过同一禁止操作。shell 解析失败与配置失败 SHALL 同样通过普通工具错误通道返回。搜索中的候选级 deny、人工拒绝或缺少授权通道 SHALL 仅排除对应候选，按 core-tools 的部分成功合同报告 permission_limited 与 skipped_files，即使全部候选被排除也不转换为整次失败；搜索调用级参数、配置或权限检查错误仍 SHALL 返回失败。

#### Scenario: 被拒后选择获准策略
- **WHEN** 模型请求的工具被拒绝，随后改用一个符合权限的操作
- **THEN** 原拒绝及后续真实结果都与调用配对进入历史，任务可以正常完成

#### Scenario: 无人工交互通道
- **WHEN** 调用需要授权，但运行环境没有可用的人工决策通道且没有有效批准记录
- **THEN** 单文件、shell 或 MCP 调用返回明确拒绝；搜索排除需要该授权的候选并报告受限范围；均不默认批准、不无限等待，也不把其他预先输入的任务当作批准

#### Scenario: 外部调用拒绝后继续任务
- **WHEN** 用户拒绝一个 MCP 调用，其他调用仍获准且任务未收到本地取消信号
- **THEN** 该外部调用返回 permission_denied 和 not_started=true，不发送执行请求；模型在剩余预算内收到结果并可调整，普通拒绝不取消本轮
