(() => {
  async function http(method, path, body) {
    let response;
    try {
      response = await fetch('/api' + path, {
        method,
        headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
        cache: 'no-store',
      });
    } catch {
      throw new Error('Cannot reach the server. Check the connection, then refresh before retrying.');
    }
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.message || `Request failed (${response.status}).`);
    return data;
  }
  window.GolfAPI = {
    activeSession: async () => (await http('GET', '/sessions/active')).session,
    startSession: async () => (await http('POST', '/sessions')).session,
    endSession: async (id) => (await http('POST', `/sessions/${id}/end`)).session,
    getSessions: async () => (await http('GET', '/sessions')).sessions,
    getShots: async (id) => (await http('GET', `/shots?session_id=${id}`)).shots,
    getShot: async (id) => (await http('GET', `/shots/${id}`)).shot,
    simulateShot: async (id, scenario) => (await http('POST', '/shots/simulate', { session_id: id, scenario })).shot,
  };
})();
