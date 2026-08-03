import { Component } from 'react'

/* A render error inside one card/tab must not blank the whole SPA — show the
   message in place and let the rest of the page keep working. */
export default class ErrorBoundary extends Component {
  state = { error: null }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidUpdate(prev) {
    // new content under the boundary (tab switch, new data) gets a fresh try
    if (this.state.error && prev.resetKey !== this.props.resetKey) {
      this.setState({ error: null })
    }
  }

  render() {
    if (this.state.error) {
      return (
        <div className="card">
          <p className="err">This view failed to render: {String(this.state.error?.message || this.state.error)}</p>
        </div>
      )
    }
    return this.props.children
  }
}
