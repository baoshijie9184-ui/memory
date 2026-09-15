# 远程 Linux 服务器部署步骤

本机只负责代码与单元测试；数据库迁移、模型连通性和端到端测试均在服务器执行。

## 首次部署

```bash
sudo mkdir -p /opt/desaymem-light /etc/desaymem-light
sudo cp -a DesayMem_light/. /opt/desaymem-light/
sudo cp /opt/desaymem-light/.env.server.example /etc/desaymem-light/desaymem-light.env
sudo chmod 600 /etc/desaymem-light/desaymem-light.env
sudo vi /etc/desaymem-light/desaymem-light.env
sudo bash /opt/desaymem-light/deploy/server_install.sh
sudo ENV_FILE=/etc/desaymem-light/desaymem-light.env \
  bash /opt/desaymem-light/deploy/server_validate.sh
```

`desaymem-migrate` 采用带校验和的顺序迁移。首次执行前仍应备份数据库，并确认应用账号只能操作目标数据库中的 `desaymem_light` schema。

## 启动与检查

```bash
sudo systemctl enable --now desaymem-light-mirror.service
sudo systemctl enable --now desaymem-light-worker.service
sudo systemctl enable --now desaymem-light-api.service
sudo systemctl status desaymem-light-api.service desaymem-light-worker.service desaymem-light-mirror.service
python3 /opt/desaymem-light/deploy/smoke_test.py --base-url http://127.0.0.1:8000
```

验收脚本使用隔离的 `desaymem_smoke` tenant，会产生一次Fact抽取LLM调用。只有脚本输出 `status=ok`，且三个服务日志无持续重试或dead job，才进入云端试用。

## 出错时

```bash
sudo systemctl stop desaymem-light-api.service desaymem-light-worker.service desaymem-light-mirror.service
sudo journalctl -u desaymem-light-api.service -n 200 --no-pager
sudo journalctl -u desaymem-light-worker.service -n 200 --no-pager
sudo journalctl -u desaymem-light-mirror.service -n 200 --no-pager
```

迁移失败时不要手工修改 `schema_migrations`，先保留日志并修复具体迁移。服务与现有 `DesayMem_mem0` 使用不同schema、端口、SQLite文件和JSON目录。
