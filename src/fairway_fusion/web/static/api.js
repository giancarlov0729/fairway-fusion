(() => {
  async function http(method, path, body) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    let response;
    try {
      response = await fetch('/api' + path, {
        method,
        signal: controller.signal,
        headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
        cache: 'no-store',
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error?.message || `Request failed (${response.status}).`);
      return data;
    } catch (error) {
      if (controller.signal.aborted || error instanceof TypeError) {
        const message = controller.signal.aborted ? 'The request timed out.' : 'Cannot reach the server.';
        const failure = new Error(message + (method === 'POST' ? ' The action may have completed. Reconnect and check History before trying it again.' : ' Check the connection and reconnect.'));
        failure.connectionLost = true;
        throw failure;
      }
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }
  window.GolfAPI = {
    activeSession: async () => (await http('GET', '/sessions/active')).session,
    startSession: async () => (await http('POST', '/sessions')).session,
    endSession: async (id) => (await http('POST', `/sessions/${id}/end`)).session,
    getSessions: async () => (await http('GET', '/sessions')).sessions,
    getShots: async (id) => (await http('GET', (id == null ? '/shots' : `/shots?session_id=${id}`))).shots,
    getShot: async (id) => (await http('GET', `/shots/${id}`)).shot,
    simulateShot: async (id, scenario) => (await http('POST', '/shots/simulate', { session_id: id, scenario })).shot,
  };
})();
