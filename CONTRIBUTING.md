# 团队协作与本地启动

团队仓库：<https://github.com/Wrestle7/agentforge-healthcare-team>（私有）

上游项目：<https://github.com/rohanthomas1202/agentforge-healthcare>

团队默认分支：`main`

## 新成员首次使用

先接受仓库邀请，再在自己的电脑上克隆。下面是 Windows CMD 命令；父目录可自行替换。

```cmd
cd /d D:\project\agent
git clone https://github.com/Wrestle7/agentforge-healthcare-team.git
cd agentforge-healthcare-team
git remote add upstream https://github.com/rohanthomas1202/agentforge-healthcare.git
copy .env.example .env
```

已有 `.env` 时跳过复制，避免覆盖本机配置。在 `.env` 中选择 `LLM_PROVIDER`（`deepseek`、`openai` 或 `anthropic`），只填写对应的 API Key。`LLM_MODEL` 和 `LLM_BASE_URL` 留空会使用该服务商的代码默认值；自定义这两项后，切换服务商时须一起检查。

`.env`、本地 SQLite、OAuth 注册响应和数据库备份不纳入 Git。团队仓库从清理后的代码快照建立，不导入上游旧提交中的数据库；原始 Git 历史保留在上游及维护者本机。应用首次启动会自动创建自己的 SQLite 数据库。

## 先运行 Mock 模式

安装 Anaconda/Miniconda 后，新成员可建立独立环境：

```cmd
conda create -n agentforge python=3.11 -y
conda activate agentforge
python -m pip install -r requirements.txt
python -X utf8 -m uvicorn app.main:app --env-file .env --host 127.0.0.1 --port 8000
```

已有 `agentforge` 环境时跳过创建。示例 `.env` 默认 `USE_MOCK_DATA=true`，不需要本地 OpenEMR；模型请求仍使用配置的服务商 API。可先用 `http://127.0.0.1:8000/api/health` 检查后端，或在 development 模式访问 `/docs` 测试接口。

## 已配置 OpenEMR 的三容器环境

需要 Docker Desktop Linux 容器和支持 `!override` 的 Docker Compose。完整环境需要各自初始化 OpenEMR、启用 FHIR、注册并启用 OAuth 客户端，将本机客户端凭据写入自己的 `.env`；代码克隆不会复制其他成员的患者库或 OAuth 凭据。

完成初始化后，在项目目录使用两个 Compose 文件：

```cmd
docker compose -f docker-compose.cloud.yml -f docker-compose.local.yml up -d --build
docker compose -f docker-compose.cloud.yml -f docker-compose.local.yml ps
docker compose -f docker-compose.cloud.yml -f docker-compose.local.yml logs --tail 100 agentforge
curl.exe --noproxy "*" -sS http://127.0.0.1:8080/api/health
curl.exe --noproxy "*" -sS http://127.0.0.1:8080/api/health/fhir
```

Web 地址：`http://127.0.0.1:8080/`；OpenEMR：`https://127.0.0.1:9300/`。Compose 会覆盖 `.env` 的部分配置，包括强制关闭 Mock、使用容器内 OpenEMR 地址。

这是本地开发配置，沿用上游演示密码。当前 MariaDB 和 OpenEMR 使用命名卷，但 AgentForge 的 `/app/data` 尚未挂载持久卷；重建 AgentForge 前须自行备份需要保留的聊天记录。运行中的数据库、真实病例和聊天记录不通过 Git 共享。种子数据脚本须按环境情况执行，避免重复导入。

## 日常改代码

开始任务前，提交或妥善保存当前未完成的修改，再创建功能分支：

```cmd
git switch main
git pull --ff-only origin main
git switch -c feat/my-task
```

修改后检查差异，只暂存本任务的具体文件（将路径换成实际修改的文件）：

```cmd
git diff
git add app/config.py
git diff --cached --stat
git diff --cached --check
git commit -m "feat: describe the change"
git push -u origin feat/my-task
```

在 GitHub 创建 `feat/my-task → main` 的 Pull Request，让另一位成员检查代码与验证结果后合并。不要共享同一个本地工作目录或强制推送他人分支。

各模块优先明确负责人：`app/agent/`（编排）、`app/tools/`（业务工具）、`app/api/`（接口）、`frontend-v2/`（Web）、`scripts/`（数据）、Compose/`deploy/`（部署）。多人需要修改同一个文件时，先协调改动范围。

## 验证与上游更新

根据改动进行离线测试或本地接口检查。`evals/` 会使用模型 API，部分场景会写 EHR；在明确配置的演示环境中运行并记录成本、模型和数据版本。当前 `tests/` 尚未提供完整单元测试覆盖。

团队仓库使用独立历史。同步上游时先获取代码并创建专门分支，再人工比较和移植相关文件，通过 PR 合入团队主分支：

```cmd
git fetch upstream
git switch main
git pull --ff-only origin main
git switch -c chore/sync-upstream
git diff --stat HEAD upstream/master -- app frontend-v2 deploy requirements.txt Dockerfile
```

不要使用 `--allow-unrelated-histories` 直接合并整个上游历史，这会重新带入旧数据库提交。检查并移植需要的代码变化后，正常提交和发起 PR；保留团队的配置与数据忽略规则。
