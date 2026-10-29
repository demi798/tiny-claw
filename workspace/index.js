/**
 * Node.js HTTP服务主入口文件
 * 使用Express框架构建RESTful API服务
 */

const express = require('express');
const app = express();
const port = process.env.PORT || 3001;

// ==================== 中间件配置 ====================

/**
 * JSON解析中间件
 * 自动解析请求体中的JSON数据
 */
app.use(express.json());

// ==================== 路由定义 ====================

/**
 * 根路径路由
 * 返回服务基本信息
 */
app.get('/', (req, res) => {
  res.json({
    code: 200,
    message: 'Node.js HTTP服务运行正常',
    data: {
      version: '1.0.0',
      timestamp: new Date().toISOString()
    }
  });
});

/**
 * 健康检查接口
 * 用于监控服务状态和运行时间
 */
app.get('/health', (req, res) => {
  res.json({
    code: 200,
    message: '服务健康状态正常',
    data: {
      status: 'healthy',
      uptime: process.uptime(),
      timestamp: new Date().toISOString()
    }
  });
});

// ==================== 错误处理 ====================

/**
 * 404错误处理中间件
 * 处理未找到的路由请求
 */
app.use((req, res) => {
  res.status(404).json({
    code: 404,
    message: '请求的资源不存在',
    data: null
  });
});

/**
 * 全局错误处理中间件
 * 捕获并处理所有未处理的错误
 */
app.use((err, req, res, next) => {
  console.error('服务器错误:', err);
  res.status(500).json({
    code: 500,
    message: '服务器内部错误',
    data: null
  });
});

// ==================== 服务器启动 ====================

/**
 * 启动HTTP服务器
 */
app.listen(port, () => {
  console.log(`🚀 服务器启动成功: http://localhost:${port}`);
  console.log('按 Ctrl+C 停止服务');
});

// 导出应用实例，支持模块化使用
module.exports = app;