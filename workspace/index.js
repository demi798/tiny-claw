const express = require('express');
const app = express();
const port = process.env.PORT || 3001;

// 中间件
app.use(express.json());

// 根路径路由
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

// 健康检查接口
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

// 404错误处理
app.use((req, res) => {
  res.status(404).json({
    code: 404,
    message: '请求的资源不存在',
    data: null
  });
});

// 全局错误处理中间件
app.use((err, req, res, next) => {
  console.error('Error:', err);
  res.status(500).json({
    code: 500,
    message: '服务器内部错误',
    data: null
  });
});

// 启动服务器
app.listen(port, () => {
  console.log(`服务器运行在 http://localhost:${port}`);
  console.log('按 Ctrl+C 停止服务');
});

module.exports = app;