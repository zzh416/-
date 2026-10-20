import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

interface SafeMarkdownProps {
  children: string
  className?: string
}

export function SafeMarkdown({ children, className }: SafeMarkdownProps) {
  return (
    <div className={className}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{
          img: ({ alt }) => <span className="blocked-media">[已隐藏图片：{alt || '图片'}]</span>,
          a: ({ href, children: linkChildren }) => {
            const isSafe = Boolean(href && /^(https?:|mailto:)/i.test(href))
            return isSafe ? (
              <a href={href} target="_blank" rel="noreferrer noopener">
                {linkChildren}
              </a>
            ) : (
              <span>{linkChildren}</span>
            )
          },
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  )
}
