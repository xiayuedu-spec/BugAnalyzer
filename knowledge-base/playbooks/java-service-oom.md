---
title: Java 服务 OOM / 内存问题排查
tags: [java, oom, memory, gc]
---

# Java 服务内存问题排查模式

适用：OOM 告警、频繁 Full GC、容器被 kill（OOMKilled）、堆外内存增长。

## 排查顺序

1. 先确认现象类型：容器被杀（OOMKilled）/ JVM 抛 OutOfMemoryError / 仅 GC 频繁，三者方向不同。
2. `buganalyzer_fetch_logs` 找 OOM/GC 相关日志：pattern `OutOfMemoryError|OOMKilled|Full GC`，since 用告警时间。
3. `buganalyzer_ssh_run` 用 `jps` 确认 JVM PID；Phase 2 用 arthas `dashboard`/`thread` 看内存与线程。
4. 本地代码：查大对象缓存、无界队列、连接池、静态集合、DirectBuffer 使用。
5. 看最近 git 改动：是否引入新缓存/集合/线程/大对象。

## 常见根因

- 堆太小 / -Xmx 配置不当（对照容器内存上限）
- 无界缓存 / 静态集合只增不减
- 连接池 / 线程池泄漏
- 堆外内存（DirectBuffer）未释放
- 反序列化/大响应体一次性加载

## 验证方法

- 修复后观察一个完整业务周期（高峰）的 GC 曲线与 RSS 是否回落
- 压测复现路径，确认内存不再增长
