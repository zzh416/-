import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App.tsx'
import './index.css'

const queryClient = new QueryClient()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ConfigProvider locale={zhCN} theme={{
      token: {
        colorPrimary: '#245fc5',
        colorInfo: '#245fc5',
        colorText: '#203149',
        colorTextSecondary: '#617087',
        colorBgLayout: '#f5f7fa',
        colorBorder: '#dfe5ed',
        borderRadius: 8,
        controlHeight: 36,
        fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif",
      },
      components: {
        Button: { primaryShadow: 'none' },
        Card: { headerFontSize: 15 },
        Tabs: { titleFontSize: 15 },
      },
    }}>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </ConfigProvider>
  </StrictMode>,
)
