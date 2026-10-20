/* Backend-free mounted production surfaces for dependency lifecycle regressions. */
import React, { useState } from 'react'
import { createRoot } from 'react-dom/client'
import '../../../index.css'
import '../ChatView.css'
import '../QuestionCard.css'
import '../../Drawer/Drawer.css'
import GoalPlanDetails from '../GoalPlanDetails.jsx'
import ProgressRail from '../ProgressRail.jsx'
import GoalHistoryCard from '../GoalHistoryCard.jsx'
import { Flag } from '@openai/apps-sdk-ui/components/Icon'
import QuestionCard from '../QuestionCard.jsx'
import WaitHistoryCard from '../WaitHistoryCard.jsx'
import MsgContent from '../MsgContent.jsx'
import ErrorCard from '../ErrorCard.jsx'
import DrawerStatusMarker from '../../Drawer/DrawerStatusMarker.jsx'
const chats = [{ chat_id: 'dependent', title: 'Finish the blocked update' }, { chat_id: 'launch', title: 'Prepare the launch' }]
const dependency = { id: 'd', target_kind: 'owner_card', condition: 'Choose the exact release to install after preserving local work and checking the host', source_chat_id: 'source', source_question_id: 'choice', source_chat_title: 'Restart and update recovery', automatic: true }
window.navigations = []
window.resumes = 0
window.withdrawals = []
window.failWithdrawal = false
const navigate = url => window.navigations.push(url.href)
function Fixture({ held = false, receiptCondition = dependency.condition, wrapped = false, kind = 'owner_card', comparisons = false } = {}) {
  const [withdrawn, setWithdrawn] = useState(false)
  const stopFollowing = async id => {
    window.withdrawals.push(id)
    if (window.failWithdrawal) throw new Error('Could not stop following. Try again.')
    setWithdrawn(true)
  }
  const active = { ...dependency, target_kind: kind, automatic: !held, resume_blocker: held ? 'goal_held' : null }
  const receipt = <WaitHistoryCard onInternalNav={navigate} summary={{ ...active, condition: receiptCondition,
    disposition: 'delivered', result_disposition: 'delivered', received_run_id: 'received',
    outcome: { kind: 'closed_without_reply', answers: { release: 'Not now' } } }} />
  return <main className="chat" style={{ maxWidth: 680, height: 'auto', margin: '24px auto', padding: 16, fontFamily: 'var(--font)', color: 'var(--text)' }}>
    <h2>Goal handoffs</h2>
    <div style={{ display: 'flex', gap: 20, margin: '16px 0' }}>
      <span><DrawerStatusMarker waiting /> Waiting</span>
      <span><DrawerStatusMarker onHold /> On hold</span>
      <span><DrawerStatusMarker ownerRequired /> Your input</span>
    </div>
    <QuestionCard chatId="source" questionId="choice" questions={[{ question: 'Which release should we install?', options: [{ id: 'later', label: 'Not now' }] }]} neededBy={chats} onInternalNav={navigate} />
    <section aria-label="Dependent Goal" style={{ marginTop: 12 }}><ProgressRail ariaLabel="Goal progress" items={[{
      key: 'goal', label: 'Waiting · Finish the blocked update', icon: <Flag width={14} height={14} aria-hidden="true" />, expandable: true,
      details: <GoalPlanDetails dependencies={withdrawn ? [] : [active]} dependencyHistory={withdrawn ? [{ ...active, disposition: 'withdrawn' }] : []} onInternalNav={navigate} onStopFollowing={stopFollowing} />,
    }]} /></section>
    <div className="chat__msg chat__msg--assistant" data-testid="receipt">
      {wrapped ? <div className="chat__answer-cause">{receipt}</div> : receipt}
    </div>
    {comparisons && <div className="chat__msg chat__msg--assistant" data-testid="siblings">
      <WaitHistoryCard summary={{ ...dependency, condition: 'Release choice', disposition: 'delivered', outcome: { kind: 'closed_without_reply' } }} />
      <div className="chat__answer-cause"><WaitHistoryCard summary={{ id: 'native', description: 'Build finishes', status: 'met' }} /></div>
      <GoalHistoryCard summary={{ id: 'finished', status: 'completed', objective: 'Repair verified' }} />
    </div>}
    {comparisons && <div data-testid="message-history">
      <MsgContent chatId="dependent" onInternalNav={navigate} msg={{ id: 'history', role: 'assistant', content: 'Done.',
        goal_summaries: [{ id: 'finished-with-dependency', status: 'completed', objective: 'Repair verified',
          plan: { tasks: [{ id: 'done', title: 'Repair verified', status: 'completed' }] },
          dependency_history: [{ ...active, disposition: 'delivered' }] }],
        wait_summaries: [{ ...active, id: 'woke', disposition: 'delivered', result_disposition: 'delivered', received_run_id: 'received',
          outcome: { kind: 'closed_without_reply', answers: { release: 'Not now' } } },
          { ...active, id: 'stopped', disposition: 'withdrawn' }] }} />
    </div>}
    <ErrorCard block={{ code: 'goal_settlement_unfinished', resumable: true }}>
      <button className="chat__resume chat__recovery-action" onClick={() => { window.resumes++ }}>Resume</button>
    </ErrorCard>
  </main>
}
const root = createRoot(document.getElementById('root'))
window.mountFixture = props => root.render(<Fixture {...props} />)
window.mountFixture({})
