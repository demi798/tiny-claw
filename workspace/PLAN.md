# Node.js HTTP服务架构规划

## 项目概述
创建一个基于Node.js的HTTP服务，使用Express框架提供RESTful API接口。

## 技术选型
- **运行时环境**: Node.js
- **Web框架**: Express.js
- **项目结构**: 
  - src/ - 源代码目录
  - package.json - 项目配置
  - index.js - 主入口文件
  - routes/ - 路由定义
  - middleware/ - 中间件
  - utils/ - 工具函数

## 功能设计
1. 基础HTTP服务器启动
2. 根路径路由 (/)
3. 健康检查接口 (/health)
4. 404错误处理
5. 全局错误处理中间件
6. JSON响应格式统一

## 部署考虑
- 端口配置可通过环境变量设置
- 支持生产环境和开发环境
- 基础的日志记录

## 代码规范
- 使用ES6+语法
- 统一的错误响应格式 { code: number, message: string, data?: any }
- 中文错误信息
- 模块化设计