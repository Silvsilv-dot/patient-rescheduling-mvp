import React, { useState, useEffect } from 'react';

const API_BASE_URL = import.meta.env.VITE_BACKEND_URL || '';

export default function StaffDashboard() {
  const [appointments, setAppointments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filterStatus, setFilterStatus] = useState('all');
  const [searchTerm, setSearchTerm] = useState('');
  const [toast, setToast] = useState(null);

  const showToast = (message, type = 'success') => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3000);
  };

  const fetchAppointments = async () => {
    try {
      setLoading(true);
      const res = await fetch(`${API_BASE_URL}/api/appointments`);
      if (!res.ok) throw new Error('Failed to fetch appointment roster.');
      const data = await res.json();
      setAppointments(data);
    } catch (err) {
      showToast(err.message, 'error');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchAppointments();
  }, []);

  const handleStatusUpdate = async (appointmentId, newStatus) => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/appointments/${appointmentId}/status`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: newStatus }),
      });

      if (!res.ok) throw new Error('Could not update appointment status.');
      
      showToast(`Appointment status updated to ${newStatus}.`);
      fetchAppointments();
    } catch (err) {
      showToast(err.message, 'error');
    }
  };

  const filteredAppointments = appointments.filter((app) => {
    const matchesStatus = filterStatus === 'all' || app.status === filterStatus;
    const matchesSearch = app.patient_name?.toLowerCase().includes(searchTerm.toLowerCase()) ||
                          app.clinician_name?.toLowerCase().includes(searchTerm.toLowerCase());
    return matchesStatus && matchesSearch;
  });

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      {toast && (
        <div className={`p-4 rounded-md text-white font-medium shadow-md ${toast.type === 'error' ? 'bg-red-600' : 'bg-green-600'}`}>
          {toast.message}
        </div>
      )}

      <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Front-Desk Staff Dashboard</h1>
          <p className="text-sm text-gray-500">Manage patient schedules, confirm bookings, and process cancellations.</p>
        </div>
        <button 
          onClick={fetchAppointments} 
          className="px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 transition"
        >
          Refresh Roster
        </button>
      </div>

      <div className="flex flex-col sm:flex-row gap-4 bg-gray-50 p-4 rounded-lg border border-gray-200">
        <input
          type="text"
          placeholder="Search by patient or clinician..."
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          className="p-2 border border-gray-300 rounded-md flex-grow focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <select
          value={filterStatus}
          onChange={(e) => setFilterStatus(e.target.value)}
          className="p-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          <option value="all">All Statuses</option>
          <option value="scheduled">Scheduled</option>
          <option value="confirmed">Confirmed</option>
          <option value="cancelled">Cancelled</option>
        </select>
      </div>

      {loading ? (
        <div className="text-center py-12 text-gray-500">Loading appointment data...</div>
      ) : (
        <div className="overflow-x-auto bg-white rounded-lg shadow border border-gray-200">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-gray-100 border-b border-gray-200 text-xs font-semibold text-gray-600 uppercase">
                <th className="p-4">ID</th>
                <th className="p-4">Patient</th>
                <th className="p-4">Clinician</th>
                <th className="p-4">Date & Time</th>
                <th className="p-4">Status</th>
                <th className="p-4">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 text-sm">
              {filteredAppointments.length === 0 ? (
                <tr>
                  <td colSpan="6" className="p-4 text-center text-gray-500">No matching appointments found.</td>
                </tr>
              ) : (
                filteredAppointments.map((app) => (
                  <tr key={app.id} className="hover:bg-gray-50">
                    <td className="p-4 font-mono text-xs">{app.id}</td>
                    <td className="p-4 font-medium text-gray-900">{app.patient_name}</td>
                    <td className="p-4 text-gray-600">{app.clinician_name}</td>
                    <td className="p-4 text-gray-600">{new Date(app.appointment_time).toLocaleString()}</td>
                    <td className="p-4">
                      <span className={`px-2 py-1 rounded-full text-xs font-semibold ${
                        app.status === 'confirmed' ? 'bg-green-100 text-green-800' :
                        app.status === 'cancelled' ? 'bg-red-100 text-red-800' : 'bg-yellow-100 text-yellow-800'
                      }`}>
                        {app.status}
                      </span>
                    </td>
                    <td className="p-4 space-x-2">
                      <button
                        onClick={() => handleStatusUpdate(app.id, 'confirmed')}
                        className="px-2 py-1 bg-green-600 text-white rounded hover:bg-green-700 text-xs"
                      >
                        Confirm
                      </button>
                      <button
                        onClick={() => handleStatusUpdate(app.id, 'cancelled')}
                        className="px-2 py-1 bg-red-600 text-white rounded hover:bg-red-700 text-xs"
                      >
                        Cancel
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
