# Renderer Service

隔离的 Playwright 动态网页渲染服务。主后端在静态正文提取不足时通过内部网络调用它。

从仓库根目录运行测试：

```powershell
npm.cmd --prefix services/renderer test
```

安全限制、只读文件系统、网络和资源配置位于 `infra/docker-compose.yml`。
