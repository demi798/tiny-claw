# Node HTTP Service

一个简单的Node.js HTTP服务，使用Express框架构建。

## 功能特性

- RESTful API接口
- 用户认证功能
- 并发安全处理
- 错误处理机制
- JSON格式响应

## 项目结构

```
.
├── auth.py          # 认证逻辑
├── index.js         # 主服务入口
├── package.json     # 项目依赖配置
├── test.py          # 并发安全测试
├── CONCURRENCY_ANALYSIS.md  # 并发安全问题分析
└── README.md        # 项目文档
```

## 安装与运行

### 前置条件
- Node.js (v14+)
- Python 3.7+

### 安装依赖
```bash
npm install
```

### 启动服务
```bash
npm start  # 生产环境
# 或
npm run dev  # 开发环境（自动重启）
```

### 运行测试
```bash
python test.py
```

## API接口

### 用户登录
- **URL**: `/login`
- **Method**: POST
- **Request Body**: 
  ```json
  {
    "username": "string",
    "password": "string"
  }
  ```
- **Response**: 
  ```json
  {
    "code": 200,
    "message": "登录成功",
    "data": {
      "token": "string"
    }
  }
  ```

### 获取用户信息
- **URL**: `/user`
- **Method**: GET
- **Headers**: 
  ```
  Authorization: Bearer <token>
  ```
- **Response**: 
  ```json
  {
    "code": 200,
    "message": "获取用户信息成功",
    "data": {
      "username": "string",
      "role": "string"
    }
  }
  ```

## 并发安全

本项目特别关注并发安全问题，在`test.py`中实现了线程安全的计数器示例，确保在高并发场景下的数据一致性。

## 错误处理

所有API接口都遵循统一的错误处理机制，返回标准化的JSON格式响应：
```json
{
  "code": 400/401/500,
  "message": "错误描述信息"
}
```

## 开发规范

- 所有API接口返回JSON格式，包含`code`和`message`字段
- 错误处理使用中文报错信息
- 遵循RESTful设计原则
- 考虑并发安全性和性能优化

## 贡献指南

1. Fork项目
2. 创建功能分支
3. 提交更改
4. 创建Pull Request

## 许可证

MIT License