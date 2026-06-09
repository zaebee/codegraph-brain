import { StrictMode } from 'react'
import ReactDOM from 'react-dom/client'
import { ReactFlowProvider } from '@xyflow/react'
import './tokens.css'
import './index.css'
import { GraphProvider } from './providers/GraphProvider'
import GraphShell from './components/GraphShell'

const rootElement = document.getElementById('root')
if (!rootElement) throw new Error('Failed to find the root element')

ReactDOM.createRoot(rootElement).render(
  <StrictMode>
    <ReactFlowProvider>
      <GraphProvider>
        <GraphShell />
      </GraphProvider>
    </ReactFlowProvider>
  </StrictMode>
)
