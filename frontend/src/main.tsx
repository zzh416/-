import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App.tsx'
import './index.css'
import './workspace.css'

const queryClient = new QueryClient()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ConfigProvider locale={zhCN} theme={{
      token: {
        colorPrimary: '#296454',
        colorInfo: '#41658c',
        colorSuccess: '#38775e',
        colorWarning: '#a26b22',
        colorError: '#be4b46',
        colorText: '#273b36',
        colorTextSecondary: '#66726d',
        colorBgLayout: '#f7f8f4',
        colorBorder: '#d8dfd8',
        colorBorderSecondary: '#e5e9e2',
        fontSize: 15,
        borderRadius: 8,
        controlHeight: 40,
        fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif",
      },
      components: {
        Button: { primaryShadow: 'none', defaultShadow: 'none', fontWeight: 500 },
        Card: { headerFontSize: 15 },
        Tabs: { titleFontSize: 15 },
        Drawer: { footerPaddingBlock: 16 },
      },
    }}>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </ConfigProvider>
  </StrictMode>,
)
