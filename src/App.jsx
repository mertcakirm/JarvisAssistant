import { useState } from 'react';

function App() {
  const [count, setCount] = useState(0);

  const appStyle = {
    display: 'flex',
    flexDirection: 'column',
    justifyContent: 'center',
    alignItems: 'center',
    minHeight: '100vh',
    backgroundColor: '#1a1a1a', // Dark background
    color: '#00ff00', // Neon green text
    fontFamily: 'monospace',
    border: '2px solid #00ff00', // Neon border for the entire app
    padding: '20px',
    boxSizing: 'border-box',
  };

  const counterContainerStyle = {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    padding: '40px',
    border: '3px solid #00ff00', // Thicker neon border
    borderRadius: '15px',
    boxShadow: '0 0 20px #00ff00, 0 0 30px #00ff00 inset', // Neon glow effect
    backgroundColor: '#0d0d0d',
  };

  const countTextStyle = {
    fontSize: '5em',
    margin: '20px 0',
    textShadow: '0 0 10px #00ff00', // Text glow
  };

  const buttonContainerStyle = {
    display: 'flex',
    gap: '20px',
    marginTop: '30px',
  };

  const buttonStyle = {
    backgroundColor: 'transparent',
    color: '#00ff00',
    border: '2px solid #00ff00',
    borderRadius: '8px',
    padding: '15px 30px',
    fontSize: '1.2em',
    cursor: 'pointer',
    transition: 'all 0.3s ease',
    textShadow: '0 0 5px #00ff00',
    boxShadow: '0 0 10px #00ff00 inset',
  };

  const buttonHoverStyle = {
    backgroundColor: '#00ff00',
    color: '#1a1a1a',
    boxShadow: '0 0 20px #00ff00',
  };

  // Helper to handle hover styles for inline styles
  const handleMouseEnter = (e) => {
    Object.assign(e.target.style, buttonHoverStyle);
  };

  const handleMouseLeave = (e) => {
    Object.assign(e.target.style, buttonStyle);
  };

  return (
    <div style={appStyle}>
      <div style={counterContainerStyle}>
        <h1 style={{ fontSize: '3em', textShadow: '0 0 15px #00ff00' }}>Neon Counter</h1>
        <div style={countTextStyle}>{count}</div>
        <div style={buttonContainerStyle}>
          <button
            style={buttonStyle}
            onClick={() => setCount(count => count - 1)}
            onMouseEnter={handleMouseEnter}
            onMouseLeave={handleMouseLeave}
          >
            Decrement
          </button>
          <button
            style={buttonStyle}
            onClick={() => setCount(0)}
            onMouseEnter={handleMouseEnter}
            onMouseLeave={handleMouseLeave}
          >
            Reset
          </button>
          <button
            style={buttonStyle}
            onClick={() => setCount(count => count + 1)}
            onMouseEnter={handleMouseEnter}
            onMouseLeave={handleMouseLeave}
          >
            Increment
          </button>
        </div>
      </div>
    </div>
  );
}

export default App;